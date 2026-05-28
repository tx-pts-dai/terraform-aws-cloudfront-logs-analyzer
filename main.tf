## Providers
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

## Locals
locals {
  distributions_ids     = join(",", var.cloudfront_distribution.ids)
  distribution_ids_name = length(var.cloudfront_distribution.ids) == 1 ? var.cloudfront_distribution.ids[0] : "multiple-ids"
  athena_workgroup_name = coalesce(var.athena_workgroup.name, "cloudfront-logs-${local.distribution_ids_name}")

  s3_results_bucket_name   = lower(var.s3_results_bucket.name != null ? var.s3_results_bucket.name : "cloudfront-${local.distribution_ids_name}-log-analytic-results")
  s3_results_bucket_prefix = var.s3_results_bucket.output_prefix != null ? var.s3_results_bucket.output_prefix : "athena-results/"

  # Normalize logs_prefix to "" or "prefix/" so callers can safely write "${bucket}/${prefix}..." without producing leading or double slashes
  s3_parquet_bucket_sanitized_prefix = var.s3_parquet_bucket.logs_prefix == "" ? "" : "${trim(var.s3_parquet_bucket.logs_prefix, "/")}/"

  common_tags = {
    Environment      = var.environment
    CF_Distributions = local.distributions_ids
    CF_Name          = var.cloudfront_distribution.name != null ? var.cloudfront_distribution.name : "N/A"
  }
}

## S3 Bucket for Analysis Results (optional)
module "s3_bucket_results" {
  source  = "terraform-aws-modules/s3-bucket/aws"
  version = "5.10.0"

  create_bucket = var.s3_results_bucket.create

  bucket = local.s3_results_bucket_name

  versioning = {
    enabled = false
  }

  lifecycle_rule = var.s3_results_bucket.lifecycle_rules

  server_side_encryption_configuration = {
    rule = {
      apply_server_side_encryption_by_default = {
        sse_algorithm = "AES256"
      }
    }
  }

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true

  tags = merge(
    var.tags,
    local.common_tags,
    {
      Name = local.s3_results_bucket_name
    }
  )
}

## Grafana Integration (optional)
data "aws_iam_policy_document" "grafana_athena_assume_role_policy" {
  count = var.grafana_access.create ? 1 : 0

  statement {
    effect = "Allow"
    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
    ]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }

    dynamic "principals" {
      for_each = length(var.grafana_access.trusted_role_arns) > 0 ? [""] : []
      content {
        type        = "AWS"
        identifiers = var.grafana_access.trusted_role_arns
      }
    }
  }
}
resource "aws_iam_role" "grafana" {
  count = var.grafana_access.create ? 1 : 0

  name        = var.grafana_access.name
  description = "Role for Grafana Athena Logs Analyser"

  assume_role_policy = data.aws_iam_policy_document.grafana_athena_assume_role_policy[0].json

  tags = merge(
    var.tags,
    local.common_tags,
    {
      Name = var.grafana_access.name
    }
  )
}


data "aws_iam_policy_document" "grafana_athena_query_policy" {
  count = var.grafana_access.create ? 1 : 0

  # Permissions for Athena
  statement {
    sid    = "AllowAthenaListAccountWide"
    effect = "Allow"
    actions = [
      "athena:ListDataCatalogs",
      "athena:ListWorkGroups",
    ]
    resources = ["*"]
  }

  # Permissions to read from the AWS Data Catalog and Glue Catalog
  statement {
    sid    = "AllowAthenaDataCatalogRead"
    effect = "Allow"
    actions = [
      "athena:ListDatabases",
      "athena:ListTableMetadata",
      "athena:GetTableMetadata",
    ]
    resources = [
      "arn:aws:athena:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:datacatalog/AwsDataCatalog",
    ]
  }

  # Permissions to run queries in the specific Athena workgroup and access query results
  statement {
    sid    = "AllowAthenaWorkgroupQueries"
    effect = "Allow"
    actions = [
      "athena:GetWorkGroup",
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetQueryResultsStream",
    ]
    resources = [
      "arn:aws:athena:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:workgroup/${local.athena_workgroup_name}",
    ]
  }

  # Permissions to read from the AWS Data Catalog and Glue Catalog
  statement {
    sid    = "AllowGlueCatalogRead"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
    ]
    resources = concat(
      [
        "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:catalog",
        aws_glue_catalog_database.cloudfront_logs.arn,
        aws_glue_catalog_table.cloudfront_logs_parquet.arn,
      ],
      [for t in aws_glue_catalog_table.ip_whitelist : t.arn],
      [for t in aws_glue_catalog_table.ip_geolocation : t.arn],
    )
  }

  # Permissions to read from the S3 bucket where CloudFront logs are stored and write query results to the Athena results bucket
  statement {
    sid    = "AllowListSourceBucket"
    effect = "Allow"
    actions = [
      "s3:GetBucketLocation",
      "s3:ListBucket",
    ]
    resources = [
      "arn:aws:s3:::${var.s3_parquet_bucket.name}",
    ]
  }

  # Permissions to read objects from the S3 bucket where CloudFront logs are stored
  statement {
    sid     = "AllowReadSourceBucketObjects"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      "arn:aws:s3:::${var.s3_parquet_bucket.name}/${local.s3_parquet_bucket_sanitized_prefix}*",
    ]
  }

  # Permissions to write query results to the Athena results bucket and read from it
  statement {
    sid    = "AllowAccessToAthenaResultsBucket"
    effect = "Allow"
    actions = [
      "s3:GetBucketLocation",
      "s3:GetObject",
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
      "s3:ListMultipartUploadParts",
      "s3:AbortMultipartUpload",
      "s3:PutObject",
    ]
    resources = [
      "arn:aws:s3:::${local.s3_results_bucket_name}",
      "arn:aws:s3:::${local.s3_results_bucket_name}/*",
    ]
  }
}
resource "aws_iam_policy" "grafana_athena_query_policy" {
  count = var.grafana_access.create ? 1 : 0

  name        = "${var.grafana_access.name}-grafana_athena_query"
  description = "Policy for Grafana Athena Logs Analyser role, granting permissions to run Athena queries and access S3 buckets for CloudFront logs analysis"

  policy = data.aws_iam_policy_document.grafana_athena_query_policy[0].json

  tags = merge(
    var.tags,
    local.common_tags,
    {
      Name = "${var.grafana_access.name}-grafana_athena_query"
    }
  )
}
resource "aws_iam_role_policy_attachment" "attach_grafana_athena_policy" {
  count = var.grafana_access.create ? 1 : 0

  role       = aws_iam_role.grafana[0].name
  policy_arn = aws_iam_policy.grafana_athena_query_policy[0].arn
}

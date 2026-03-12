## Providers
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.0"
    }
  }
}

## Locals
locals {
  distrubutions_ids     = join(",", var.cloudfront_distribution.ids)
  distribution_ids_name = length(var.cloudfront_distribution.ids) == 1 ? var.cloudfront_distribution.ids[0] : "multiple-ids"
  athena_workgroup_name = var.athena_workgroup.create ? (var.athena_workgroup.name != null ? var.athena_workgroup.name : "cloudfront-logs-${local.distribution_ids_name}") : null

  s3_results_bucket_name   = lower(var.s3_results_bucket.name != null ? var.s3_results_bucket.name : "cloudfront-${local.distribution_ids_name}-log-analytic-results")
  s3_results_bucket_prefix = var.s3_results_bucket.output_prefix != null ? var.s3_results_bucket.output_prefix : "athena-results/"

  s3_preprocessing_bucket_name   = lower(var.user_agents_preprocessing.bucket.name != null ? var.user_agents_preprocessing.bucket.name : "cloudfront-${local.distribution_ids_name}-log-analytic-preprocessing")
  s3_preprocessing_bucket_prefix = var.user_agents_preprocessing.bucket.output_prefix != null ? var.user_agents_preprocessing.bucket.output_prefix : "preprocessed-data/"

  # Remove trailing slash if exists and leading slash if exists to avoid double slashes in S3 paths
  s3_parquet_bucket_sanitized_prefix      = replace(var.s3_parquet_bucket.logs_prefix, "^/|/$", "")
  s3_preprocessed_bucket_sanitized_prefix = replace(local.s3_preprocessing_bucket_prefix, "^/|/$", "")

  common_tags = {
    Environment      = var.environment
    CF_Distributions = local.distrubutions_ids
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

module "s3_bucket_preprocessing" {
  source  = "terraform-aws-modules/s3-bucket/aws"
  version = "5.10.0"

  create_bucket = var.user_agents_preprocessing.bucket.create

  bucket = local.s3_preprocessing_bucket_name

  versioning = {
    enabled = false
  }

  lifecycle_rule = var.user_agents_preprocessing.bucket.lifecycle_rules

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
resource "aws_iam_role" "grafana" {
  count = var.grafana_access.create ? 1 : 0

  name = "cloudfront-logs-grafana-access"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "athena.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(
    var.tags,
    local.common_tags,
    {
      Name = "cloudfront-logs-grafana-access"
    }
  )
}
resource "aws_iam_policy" "grafana_access" {
  count = var.grafana_access.create ? 1 : 0

  name        = "cloudfront-logs-grafana-access"
  description = "Policy to allow Grafana to access Athena and S3 results bucket for CloudFront logs analysis"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        "Sid" : "ReadWriteResultsBucket",
        "Effect" : "Allow",
        "Action" : [
          "s3:Get*",
          "s3:List*",
          "s3:PutObject"
        ],
        "Resource" : [
          "arn:aws:s3:::${local.s3_results_bucket_name}/",
          "arn:aws:s3:::${local.s3_results_bucket_name}/*",
          "arn:aws:s3:::${local.s3_preprocessing_bucket_name}/",
          "arn:aws:s3:::${local.s3_preprocessing_bucket_name}/*"
        ]
      },
      {
        "Sid" : "ReadOnlyLogsAndPreprocessingBucket",
        "Effect" : "Allow",
        "Action" : [
          "s3:Get*"
        ],
        "Resource" : [
          "arn:aws:s3:::${var.s3_parquet_bucket.name}/${local.s3_parquet_bucket_sanitized_prefix}/",
          "arn:aws:s3:::${var.s3_parquet_bucket.name}/${local.s3_parquet_bucket_sanitized_prefix}/*",
          "arn:aws:s3:::${local.s3_preprocessing_bucket_name}/${local.s3_preprocessed_bucket_sanitized_prefix}/",
          "arn:aws:s3:::${local.s3_preprocessing_bucket_name}/${local.s3_preprocessed_bucket_sanitized_prefix}/*"
        ]
      }
    ]
  })

  tags = merge(
    var.tags,
    local.common_tags,
    {
      Name = "cloudfront-logs-grafana-access"
    }
  )
}

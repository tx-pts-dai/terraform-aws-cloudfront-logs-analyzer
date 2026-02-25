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

## Locals
locals {
  common_tags = {
    Environment     = var.environment
    CF_Distribution = var.cloudfront_distribution.id
    CF_Name         = var.cloudfront_distribution.name != null ? var.cloudfront_distribution.name : "N/A"
  }

  s3_results_bucket_name   = lower(var.s3_results_bucket.name != null ? var.s3_results_bucket.name : "cloudfront-${var.cloudfront_distribution.id}-log-analytic-results")
  s3_results_bucket_prefix = var.s3_results_bucket.output_prefix != null ? var.s3_results_bucket.output_prefix : "athena-results/"

  # Remove trailing slash if exists and leading slash if exists to avoid double slashes in S3 paths
  s3_parquet_bucket_sanitized_prefix = replace(var.s3_parquet_bucket.logs_prefix, "^/|/$", "")
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
          "arn:aws:s3:::${local.s3_results_bucket_name}/*"
        ]
      },
      {
        "Sid" : "ReadOnlyLogsBucket",
        "Effect" : "Allow",
        "Action" : [
          "s3:Get*"
        ],
        "Resource" : [
          "arn:aws:s3:::${var.s3_parquet_bucket.name}/${local.s3_parquet_bucket_sanitized_prefix}/",
          "arn:aws:s3:::${var.s3_parquet_bucket.name}/${local.s3_parquet_bucket_sanitized_prefix}/*"
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

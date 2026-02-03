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
}

## S3 Bucket for Analysis Results (optional)
module "s3_bucket_results" {
  source  = "terraform-aws-modules/s3-bucket/aws"
  version = "5.10.0"

  create_bucket = var.s3_results_bucket.create

  bucket = local.s3_results_bucket_name

  versioning = {
    enabled = true
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

resource "aws_athena_workgroup" "cloudfront_logs" {
  name        = "cloudfront-logs-${var.cloudfront_distribution.id}"
  description = "Athena workgroup for analyzing CloudFront logs for distribution ${var.cloudfront_distribution.id}"

  configuration {
    enforce_workgroup_configuration = true

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location = "s3://${local.s3_results_bucket_name}/${local.s3_results_bucket_prefix}"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

resource "aws_athena_named_query" "detect_outliers" {
  name        = "detect_outliers"
  description = "Find IPs exceeding request threshold in rolling 5-min windows"
  workgroup   = aws_athena_workgroup.cloudfront_logs.name
  database    = aws_glue_catalog_database.cloudfront_logs.name

  # load SQL from a file to keep .tf tidy:
  query = file("${path.module}/athena-queries/detect_outliers.sql")
}

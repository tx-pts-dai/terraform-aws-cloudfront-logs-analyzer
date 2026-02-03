# Glue Database
output "glue_database_name" {
  description = "Name of the Glue database"
  value       = aws_glue_catalog_database.cloudfront_logs.name
}

# Glue Tables
output "glue_table_cloudfront_logs" {
  description = "Name of the CloudFront logs table"
  value       = aws_glue_catalog_table.cloudfront_logs_parquet.name
}

# S3 Locations
output "s3_results_bucket" {
  description = "S3 bucket for analysis results"
  value       = module.s3_bucket_results.s3_bucket_id
}

# Athena named queries
output "athena_named_queries" {
  description = "Athena named queries"
  value = {
    "detect_outliers" = aws_athena_named_query.detect_outliers.id
  }
}

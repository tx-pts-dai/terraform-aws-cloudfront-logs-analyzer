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

output "grafana_role_arn" {
  description = "ARN of the IAM role for Grafana access"
  value       = one(aws_iam_role.grafana[*].arn)
}

# Summaries
output "summaries_lambda_name" {
  description = "Name of the summarizer Lambda (null when summaries are disabled)"
  value       = one(aws_lambda_function.summarizer[*].function_name)
}

output "summaries_glue_tables" {
  description = "Glue tables created for summaries (null when disabled)"
  value = var.summaries.enable ? {
    robots_agents        = aws_glue_catalog_table.robots_agents[0].name
    ua_map               = aws_glue_catalog_table.ua_map[0].name
    cf_ua_hourly         = aws_glue_catalog_table.cf_ua_hourly[0].name
    cf_bot_errors_hourly = aws_glue_catalog_table.cf_bot_errors_hourly[0].name
  } : null
}

output "summaries_failed_queue_url" {
  description = "URL of the SQS queue receiving summarizer events that failed after all retries (null when summaries are disabled)"
  value       = one(aws_sqs_queue.summarizer_failed[*].url)
}

output "summaries_alarm_arns" {
  description = "ARNs of the summarizer CloudWatch alarms (empty when summaries are disabled)"
  value       = concat(aws_cloudwatch_metric_alarm.summarizer_failed_events[*].arn, aws_cloudwatch_metric_alarm.summarizer_not_running[*].arn)
}

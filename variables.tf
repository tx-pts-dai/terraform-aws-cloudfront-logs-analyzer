variable "environment" {
  description = "Environment name (e.g., dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}

# CloudFront
variable "cloudfront_distribution" {
  description = "The ID of the CloudFront distribution"
  type = object({
    id   = optional(string, "global")
    name = optional(string, "global")
  })
}

# S3 Parquet Logs
variable "s3_parquet_bucket" {
  description = "Configuration for the existing S3 bucket where CloudFront logs in Parquet format are stored"
  type = object({
    name        = string
    logs_prefix = string
  })
}

variable "s3_results_bucket" {
  description = "Configuration for the S3 bucket where analysis results will be stored"
  type = object({
    create          = bool
    name            = optional(string)
    output_prefix   = optional(string)
    lifecycle_rules = optional(list(any), [])
  })
}

variable "glue_database" {
  description = "Name of the Glue database for CloudFront logs"
  type = object({
    name = optional(string)
  })
}

# S3 Bucket for Supporters
variable "s3_supporters_files" {
  description = "Configuration for the S3 bucket where supporter data files are stored"
  type = object({
    ip_whitelist_fullpath   = optional(string, "s3://arn/full/path/to/ip-whitelist-parquet-format/")
    ip_geolocation_fullpath = optional(string, "s3://arn/full/path/to/ip-geolocation-parquet-format/")
  })
}

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
  description = "The IDs of the CloudFront distributions to analyze logs for."
  type = object({
    name = optional(string, "global")
    ids  = list(string)
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

# Preprocessing Functions
variable "user_agents_preprocessing" {
  description = "Configuration for user agent preprocessing"
  type = object({
    enable = bool
    bucket = optional(object({
      create          = bool
      name            = optional(string)
      output_prefix   = optional(string)
      lifecycle_rules = optional(list(any), [])
      }), {
      create = false
    })
  })
  default = {
    enable = false
  }
}

# Glue Database
variable "glue_database" {
  description = "Name of the Glue database for CloudFront logs"
  type = object({
    name = optional(string)
  })
  default = {
  }
}

# S3 Bucket for Supporters
variable "s3_supporters_files" {
  description = "Configuration for the S3 bucket where supporter data files are stored"
  type = object({
    ip_whitelist_fullpath   = optional(string)
    ip_geolocation_fullpath = optional(string)
  })
  default = {
  }
}

# Grafana
variable "grafana_access" {
  description = "Configuration for Grafana integration"
  type = object({
    create            = bool
    name              = optional(string)
    custom_policy_arn = optional(string)
  })
  default = {
    create = false
  }
}

# Athena 
## Workgroup
variable "athena_workgroup" {
  description = "Configuration for the Athena workgroup"
  type = object({
    create = bool
    name   = optional(string)
  })
  default = {
    create = false
    name   = "primary"
  }
}

## Custom Named queries
variable "athena_custom_named_queries" {
  description = "List of custom Athena named queries to create"
  type = list(object({
    name             = string
    description      = optional(string)
    path_to_sql_file = string
  }))
  default = []
}

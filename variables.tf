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
    create                  = bool
    name                    = optional(string)
    output_prefix           = optional(string)
    results_expiration_days = optional(number, 30)
    lifecycle_rules         = optional(list(any), [])
  })
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
    ip_whitelist_format     = optional(string, "parquet")
    ip_geolocation_fullpath = optional(string)
  })
  default = {
  }
  validation {
    condition     = contains(["parquet", "json", "csv"], var.s3_supporters_files.ip_whitelist_format)
    error_message = "Must be one of: parquet, json, csv"
  }
}

# Grafana
variable "grafana_access" {
  description = "Configuration for Grafana integration"
  type = object({
    create            = bool
    name              = optional(string, "cloudfront-logs-grafana-access")
    trusted_role_arns = optional(list(string), [])
    custom_policy_arn = optional(string)
  })
  default = {
    create = false
  }

  validation {
    condition     = !(var.grafana_access.create) || (length(var.grafana_access.trusted_role_arns) > 0)
    error_message = "If grafana_access.create is true, you must provide either trusted_role_arns"
  }
}

# Athena 
## Workgroup
variable "athena_workgroup" {
  description = "Configuration for the Athena workgroup"
  type = object({
    create = bool
    name   = optional(string)
    # ~2.5x the largest query observed on the Grafana dashboards (38 GB)
    bytes_scanned_cutoff_per_query = optional(number, 107374182400)
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

# Summaries
variable "summaries" {
  description = "Hourly UA summaries (Athena UNLOAD to Parquet) and the Lambda that builds them"
  type = object({
    enable   = bool
    schedule = optional(string, "cron(20 * * * ? *)")

    robots_agents_url = optional(string)
    robots_agents = optional(list(object({
      token    = string
      operator = string
      purpose  = string
      notes    = optional(string, "")
      })), [
      { token = "GPTBot", operator = "OpenAI", purpose = "ai-training" },
      { token = "ClaudeBot", operator = "Anthropic", purpose = "ai-training" },
      { token = "Google-Extended", operator = "Google", purpose = "ai-training", notes = "robots.txt-only token, never sent as UA" },
      { token = "Applebot-Extended", operator = "Apple", purpose = "ai-training", notes = "robots.txt-only token, never sent as UA" },
      { token = "Meta-ExternalAgent", operator = "Meta", purpose = "ai-training" },
      { token = "Googlebot", operator = "Google", purpose = "search" },
      { token = "Bingbot", operator = "Microsoft", purpose = "search" },
      { token = "Applebot", operator = "Apple", purpose = "search" },
      { token = "OAI-SearchBot", operator = "OpenAI", purpose = "ai-search" },
      { token = "PerplexityBot", operator = "Perplexity", purpose = "ai-search" },
      { token = "Claude-SearchBot", operator = "Anthropic", purpose = "ai-search" },
      { token = "Meta-WebIndexer", operator = "Meta", purpose = "ai-search" },
      { token = "ChatGPT-User", operator = "OpenAI", purpose = "ai-user" },
      { token = "Claude-User", operator = "Anthropic", purpose = "ai-user" },
      { token = "Perplexity-User", operator = "Perplexity", purpose = "ai-user" },
      { token = "Meta-ExternalFetcher", operator = "Meta", purpose = "ai-user" },
    ])
  })
  default = {
    enable = false
  }

  validation {
    condition = alltrue([
      for a in var.summaries.robots_agents :
      contains(["search", "ai-training", "ai-search", "ai-user", "ads", "other"], a.purpose)
    ])
    error_message = "robots_agents purpose must be one of: search, ai-training, ai-search, ai-user, ads, other"
  }
}

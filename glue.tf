locals {
  glue_database_name = lower(var.glue_database.name != null ? var.glue_database.name : "cloudfront_${var.cloudfront_distribution.id}_analytics")

  # CloudFront Parquet v2 schema - extracted from sample logs
  # https://docs.aws.amazon.com/athena/latest/ug/create-cloudfront-table-manual-parquet.html
  cloudfront_parquet_schema = [
    { name = "date", type = "string" },
    { name = "time", type = "string" },
    { name = "timestamp", type = "string" },    # found by analysing the sample logs
    { name = "timestamp_ms", type = "string" }, # found by analysing the sample logs
    { name = "x_edge_location", type = "string" },
    { name = "sc_bytes", type = "string" },
    { name = "c_ip", type = "string" },
    { name = "cs_method", type = "string" },
    { name = "cs_host", type = "string" },
    { name = "cs_uri_stem", type = "string" },
    { name = "sc_status", type = "string" },
    { name = "cs_referer", type = "string" },
    { name = "cs_user_agent", type = "string" },
    { name = "cs_uri_query", type = "string" },
    { name = "cs_cookie", type = "string" },
    { name = "x_edge_result_type", type = "string" },
    { name = "x_edge_request_id", type = "string" },
    { name = "x_host_header", type = "string" },
    { name = "cs_protocol", type = "string" },
    { name = "cs_bytes", type = "string" },
    { name = "time_taken", type = "string" },
    { name = "x_forwarded_for", type = "string" },
    { name = "ssl_protocol", type = "string" },
    { name = "ssl_cipher", type = "string" },
    { name = "x_edge_response_result_type", type = "string" },
    { name = "cs_protocol_version", type = "string" },
    { name = "fle_status", type = "string" },
    { name = "fle_encrypted_fields", type = "string" },
    { name = "c_port", type = "string" },
    { name = "time_to_first_byte", type = "string" },
    { name = "x_edge_detailed_result_type", type = "string" },
    { name = "sc_content_type", type = "string" },
    { name = "sc_content_len", type = "string" },
    { name = "sc_range_start", type = "string" },
    { name = "sc_range_end", type = "string" },
    { name = "c_country", type = "string" },
  ]
  ip_geolocation_schema = [
    { name = "ip", type = "string" },
    { name = "ip_version", type = "int" },
    { name = "city", type = "string" },
    { name = "region", type = "string" },
    { name = "country", type = "string" },
    { name = "hostname", type = "string" },
    { name = "org", type = "string" },
    { name = "loc", type = "string" },         # Latitude,Longitude string ("lat,lon") for simple mapping
    { name = "cached_date", type = "string" }, # Timestamp when this entry was cached/created
    { name = "source", type = "string" },      # Source of the lookup (e.g., ipinfo, maxmind) for provenance
  ]
  ip_whitelist_schema = [
    { name = "ip", type = "string" },
    { name = "ip_version", type = "int" },
    { name = "cidr_size", type = "int" }, # CIDR prefix length (e.g., 27, 32, 64)
    { name = "reason", type = "string" },
    { name = "added_date", type = "string" },
    { name = "added_by", type = "string" },
    { name = "request_limit", type = "int" }, # Optional per-network request limit (requests per 5-min window)
  ]
}

resource "aws_glue_catalog_database" "cloudfront_logs" {
  name        = local.glue_database_name
  description = "Database for CloudFront ${var.cloudfront_distribution.id} logs analysis"

  tags = merge(
    var.tags,
    local.common_tags,
    {
      Name = local.glue_database_name
    }
  )
}

# Glue Table with Partition Projection for CloudFront v2 Format
# This handles the 2026/01/26/08/ folder structure
resource "aws_glue_catalog_table" "cloudfront_logs_parquet" {
  name          = "cloudfront_logs_parquet"
  database_name = aws_glue_catalog_database.cloudfront_logs.name
  description   = "CloudFront logs in Parquet format with partition projection for distribution ${var.cloudfront_distribution.id}"

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "projection.enabled"        = "true"
    "projection.year.type"      = "integer"
    "projection.year.range"     = "2020,2046" # covers the next 20 years
    "projection.month.type"     = "integer"
    "projection.month.range"    = "1,12"
    "projection.month.digits"   = "2"
    "projection.day.type"       = "integer"
    "projection.day.range"      = "1,31"
    "projection.day.digits"     = "2"
    "projection.hour.type"      = "integer"
    "projection.hour.range"     = "0,23"
    "projection.hour.digits"    = "2"
    "storage.location.template" = "s3://${var.s3_parquet_bucket.name}/${var.s3_parquet_bucket.logs_prefix}$${year}/$${month}/$${day}/$${hour}"
    "EXTERNAL"                  = "TRUE"   # true means table is managed by us not AWS
    "parquet.compression"       = "SNAPPY" # cannot be changed, enforced by AWS
  }

  partition_keys {
    name = "year"
    type = "int"
  }

  partition_keys {
    name = "month"
    type = "int"
  }

  partition_keys {
    name = "day"
    type = "int"
  }

  partition_keys {
    name = "hour"
    type = "int"
  }

  storage_descriptor {
    location      = "s3://${var.s3_parquet_bucket.name}/${var.s3_parquet_bucket.logs_prefix}"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                  = "ParquetHiveSerDe"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    # Dynamic columns from local schema definition
    dynamic "columns" {
      for_each = local.cloudfront_parquet_schema
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }

  depends_on = [
    aws_glue_catalog_database.cloudfront_logs
  ]
}

## Glue Support Tables for IP Whitelist and Geolocation Cache
## These tables are used by Athena queries and Glue jobs for filtering and enrichment

# Whitelist/Allowed IPs Table
# Used to filter out known good IPs (e.g., search engine bots, monitoring services)
resource "aws_glue_catalog_table" "ip_whitelist" {
  count = try(length(var.s3_supporters_files.ip_whitelist_fullpath) > 0, false) ? 1 : 0

  name          = "ip_whitelist"
  database_name = aws_glue_catalog_database.cloudfront_logs.name
  description   = "Whitelist of allowed IPs with request rate limits"

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "EXTERNAL"            = "TRUE"
    "has_encrypted_data"  = "false"
    "parquet.compression" = "SNAPPY"
  }

  storage_descriptor {
    location      = var.s3_supporters_files.ip_whitelist_fullpath
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                  = "ParquetHiveSerDe"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    dynamic "columns" {
      for_each = local.ip_whitelist_schema
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }

  depends_on = [
    aws_glue_catalog_database.cloudfront_logs
  ]
}

# IP Geolocation Cache Table
# Used to cache geolocation lookups from ipinfo API to avoid repeated API calls
resource "aws_glue_catalog_table" "ip_geolocation" {
  count = try(length(var.s3_supporters_files.ip_geolocation_fullpath) > 0, false) ? 1 : 0

  name          = "ip_geolocation"
  database_name = aws_glue_catalog_database.cloudfront_logs.name
  description   = "Cache of IP geolocation data from ipinfo API"

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "EXTERNAL"            = "TRUE"
    "has_encrypted_data"  = "false"
    "parquet.compression" = "SNAPPY"
  }

  storage_descriptor {
    location      = var.s3_supporters_files.ip_geolocation_fullpath
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                  = "ParquetHiveSerDe"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    dynamic "columns" {
      for_each = local.ip_geolocation_schema
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }

  depends_on = [
    aws_glue_catalog_database.cloudfront_logs
  ]
}

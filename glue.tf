locals {
  glue_database_name = lower(var.glue_database.name != null ? var.glue_database.name : "cloudfront_${local.distribution_ids_name}_analytics")

  glue_format_configs = {
    parquet = {
      input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
      output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
      serde_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      serde_params  = { "serialization.format" = "1" }
      table_params  = { "parquet.compression" = "SNAPPY" }
    }
    json = {
      input_format  = "org.apache.hadoop.mapred.TextInputFormat"
      output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
      serde_library = "org.openx.data.jsonserde.JsonSerDe"
      serde_params  = { "serialization.format" = "1" }
      table_params  = {}
    }
    csv = {
      input_format  = "org.apache.hadoop.mapred.TextInputFormat"
      output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
      serde_library = "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
      serde_params  = { "field.delim" = ",", "serialization.format" = "," }
      table_params  = { "skip.header.line.count" = "1" }
    }
  }

  ip_whitelist_fmt = local.glue_format_configs[var.s3_supporters_files.ip_whitelist_format]
}

resource "aws_glue_catalog_database" "cloudfront_logs" {
  name        = local.glue_database_name
  description = "Database for CloudFront ${local.distribution_ids_name} logs analysis"

  tags = merge(
    var.tags,
    local.common_tags,
    {
      Name = local.glue_database_name
    }
  )
}

# Glue Table with Partition Projection for CloudFront v2 Format
# This handles the {distributionid}/{yyyy/MM/dd}/{hour}/ folder structure
# dt partition uses a date projection (yyyy/MM/dd) mapping directly to S3 path segments
resource "aws_glue_catalog_table" "cloudfront_logs_parquet" {
  name          = "cloudfront_logs_parquet"
  database_name = aws_glue_catalog_database.cloudfront_logs.name
  description   = "CloudFront logs in Parquet format with partition projection for distribution ${local.distribution_ids_name}"

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "EXTERNAL"                         = "TRUE"   # true means table is managed by us not AWS
    "parquet.compression"              = "SNAPPY" # cannot be changed, enforced by AWS
    "projection.enabled"               = "true"
    "projection.distributionid.type"   = "enum"
    "projection.distributionid.values" = local.distributions_ids
    "projection.dt.type"               = "date"
    "projection.dt.range"              = "2020/01/01,NOW"
    "projection.dt.format"             = "yyyy/MM/dd"
    "projection.dt.interval"           = "1"
    "projection.dt.interval.unit"      = "DAYS"
    "projection.hour.type"             = "integer"
    "projection.hour.range"            = "0,23"
    "projection.hour.digits"           = "2"
    "storage.location.template"        = "s3://${var.s3_parquet_bucket.name}/${local.s3_parquet_bucket_sanitized_prefix}$${distributionid}/$${dt}/$${hour}"
  }

  partition_keys {
    name = "distributionid"
    type = "string"
  }

  partition_keys {
    name = "dt"
    type = "string"
  }

  partition_keys {
    name = "hour"
    type = "int"
  }

  storage_descriptor {
    location      = "s3://${var.s3_parquet_bucket.name}/${local.s3_parquet_bucket_sanitized_prefix}"
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
      # CloudFront Parquet v2 schema - extracted from sample logs
      # https://docs.aws.amazon.com/athena/latest/ug/create-cloudfront-table-manual-parquet.html
      for_each = [
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

  parameters = merge(
    {
      "EXTERNAL"           = "TRUE"
      "has_encrypted_data" = "false"
    },
    local.ip_whitelist_fmt.table_params
  )

  storage_descriptor {
    location      = var.s3_supporters_files.ip_whitelist_fullpath
    input_format  = local.ip_whitelist_fmt.input_format
    output_format = local.ip_whitelist_fmt.output_format

    ser_de_info {
      name                  = var.s3_supporters_files.ip_whitelist_format
      serialization_library = local.ip_whitelist_fmt.serde_library
      parameters            = local.ip_whitelist_fmt.serde_params
    }

    dynamic "columns" {
      for_each = [
        { name = "ip", type = "string" },
        { name = "ip_version", type = "int" },
        { name = "cidr_size", type = "int" }, # CIDR prefix length (e.g., 27, 32, 64)
        { name = "reason", type = "string" },
        { name = "added_date", type = "string" },
        { name = "added_by", type = "string" },
        { name = "request_limit", type = "int" }, # Optional per-network request limit (requests per 5-min window)
      ]
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
      for_each = [
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

locals {
  summarizer_name = "cf-logs-summarizer-${local.distribution_ids_name}"
  summarizer_src  = "${path.module}/summarizer"
  robots_key      = "supporters/robots-agents/robots_agents.json"
  results_arn     = "arn:aws:s3:::${local.s3_results_bucket_name}"
  glue_arn_prefix = "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}"

  robots_url_enabled = var.summaries.enable && var.summaries.robots_agents_url != null

  robots_url_tokens = try(data.http.robots_txt[0].status_code, 0) == 200 ? distinct([
    for m in regexall("(?im)^user-agent:(.*)$", try(data.http.robots_txt[0].response_body, "")) : trimspace(m[0])
    if trimspace(m[0]) != "*"
  ]) : []

  robots_explicit_lower = [for a in var.summaries.robots_agents : lower(a.token)]

  robots_agents = concat(
    var.summaries.robots_agents,
    [
      for t in local.robots_url_tokens :
      { token = t, operator = "", purpose = "other", notes = "from robots_agents_url" }
      if !contains(local.robots_explicit_lower, lower(t))
    ],
  )
}

data "http" "robots_txt" {
  count = local.robots_url_enabled ? 1 : 0

  url = var.summaries.robots_agents_url

  retry {
    attempts = 2
  }
}

resource "aws_s3_object" "robots_agents" {
  count = var.summaries.enable ? 1 : 0

  bucket       = local.s3_results_bucket_name
  key          = local.robots_key
  content_type = "application/x-ndjson"
  content      = join("\n", [for a in local.robots_agents : jsonencode(a)])

  depends_on = [module.s3_bucket_results]
}

resource "aws_lambda_layer_version" "summarizer_deps" {
  count = var.summaries.enable ? 1 : 0

  filename                 = "${local.summarizer_src}/layer.zip"
  source_code_hash         = try(filebase64sha256("${local.summarizer_src}/layer.zip"), null)
  layer_name               = "${local.summarizer_name}-deps"
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
}

data "archive_file" "summarizer" {
  count = var.summaries.enable ? 1 : 0

  type        = "zip"
  output_path = "${local.summarizer_src}/function.zip"

  source {
    content  = file("${local.summarizer_src}/handler.py")
    filename = "handler.py"
  }
}

resource "aws_cloudwatch_log_group" "summarizer" {
  count = var.summaries.enable ? 1 : 0

  name              = "/aws/lambda/${local.summarizer_name}"
  retention_in_days = 14

  tags = merge(var.tags, local.common_tags, { Name = local.summarizer_name })
}

resource "aws_lambda_function" "summarizer" {
  count = var.summaries.enable ? 1 : 0

  function_name    = local.summarizer_name
  role             = aws_iam_role.summarizer[0].arn
  filename         = data.archive_file.summarizer[0].output_path
  source_code_hash = data.archive_file.summarizer[0].output_base64sha256
  handler          = "handler.handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  timeout          = 900
  memory_size      = 1024

  # ua_dim is append-only; concurrent runs would write duplicate rows
  reserved_concurrent_executions = 1

  layers = [aws_lambda_layer_version.summarizer_deps[0].arn]

  environment {
    variables = {
      DATABASE        = aws_glue_catalog_database.cloudfront_logs.name
      WORKGROUP       = local.athena_workgroup_name
      OUTPUT_LOCATION = "s3://${local.s3_results_bucket_name}/${local.s3_results_bucket_prefix}summarizer/"
      RESULTS_BUCKET  = local.s3_results_bucket_name
      ROBOTS_KEY      = local.robots_key
    }
  }

  depends_on = [aws_cloudwatch_log_group.summarizer, aws_iam_role_policy.summarizer]

  tags = merge(var.tags, local.common_tags, { Name = local.summarizer_name })
}

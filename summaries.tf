locals {
  summarizer_name = "cf-logs-summarizer-${local.distribution_ids_name}"
  summarizer_src  = "${path.module}/summarizer"
  robots_key      = "supporters/robots-agents/robots_agents.json"
  results_arn     = "arn:aws:s3:::${local.s3_results_bucket_name}"
  glue_arn_prefix = "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_object" "robots_agents" {
  count = var.summaries.enable ? 1 : 0

  bucket       = local.s3_results_bucket_name
  key          = local.robots_key
  content_type = "application/x-ndjson"
  content      = join("\n", [for a in var.summaries.robots_agents : jsonencode(a)])

  depends_on = [module.s3_bucket_results]
}

resource "terraform_data" "summarizer_pip_install" {
  count = var.summaries.enable ? 1 : 0

  triggers_replace = [filemd5("${local.summarizer_src}/requirements.txt")]

  provisioner "local-exec" {
    command = <<-CMD
      pip install \
        -r "${local.summarizer_src}/requirements.txt" \
        -t "${local.summarizer_src}/layer/python" \
        --platform manylinux2014_x86_64 \
        --python-version 3.12 \
        --only-binary=:all: \
        --upgrade --quiet
    CMD
  }
}

data "archive_file" "summarizer_layer" {
  count = var.summaries.enable ? 1 : 0

  type        = "zip"
  source_dir  = "${local.summarizer_src}/layer"
  output_path = "${local.summarizer_src}/layer.zip"

  depends_on = [terraform_data.summarizer_pip_install]
}

resource "aws_lambda_layer_version" "summarizer_deps" {
  count = var.summaries.enable ? 1 : 0

  filename                 = data.archive_file.summarizer_layer[0].output_path
  source_code_hash         = data.archive_file.summarizer_layer[0].output_base64sha256
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

data "aws_iam_policy_document" "summarizer_assume" {
  count = var.summaries.enable ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "summarizer" {
  count = var.summaries.enable ? 1 : 0

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.summarizer[0].arn}:*"]
  }

  statement {
    sid = "Athena"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
    ]
    resources = ["arn:aws:athena:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:workgroup/${local.athena_workgroup_name}"]
  }

  statement {
    sid     = "GlueRead"
    actions = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:GetPartition", "glue:GetPartitions"]
    resources = [
      "${local.glue_arn_prefix}:catalog",
      aws_glue_catalog_database.cloudfront_logs.arn,
      "${local.glue_arn_prefix}:table/${aws_glue_catalog_database.cloudfront_logs.name}/*",
    ]
  }

  statement {
    sid       = "ReadRawLogs"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.s3_parquet_bucket.name}/${local.s3_parquet_bucket_sanitized_prefix}*"]
  }

  statement {
    sid       = "ListBuckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.s3_parquet_bucket.name}", local.results_arn]
  }

  statement {
    sid = "ResultsReadWrite"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = [
      "${local.results_arn}/${local.s3_results_bucket_prefix}*",
      "${local.results_arn}/ua_dim/*",
      "${local.results_arn}/summaries/*",
    ]
  }

  statement {
    sid       = "ReadRobots"
    actions   = ["s3:GetObject"]
    resources = ["${local.results_arn}/supporters/robots-agents/*"]
  }
}

resource "aws_iam_role" "summarizer" {
  count = var.summaries.enable ? 1 : 0

  name               = local.summarizer_name
  assume_role_policy = data.aws_iam_policy_document.summarizer_assume[0].json

  tags = merge(var.tags, local.common_tags, { Name = local.summarizer_name })
}

resource "aws_iam_role_policy" "summarizer" {
  count = var.summaries.enable ? 1 : 0

  name   = local.summarizer_name
  role   = aws_iam_role.summarizer[0].id
  policy = data.aws_iam_policy_document.summarizer[0].json
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

data "aws_iam_policy_document" "summarizer_scheduler_assume" {
  count = var.summaries.enable ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "summarizer_scheduler" {
  count = var.summaries.enable ? 1 : 0

  name               = "${local.summarizer_name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.summarizer_scheduler_assume[0].json

  tags = merge(var.tags, local.common_tags, { Name = "${local.summarizer_name}-scheduler" })
}

resource "aws_iam_role_policy" "summarizer_scheduler" {
  count = var.summaries.enable ? 1 : 0

  name = "invoke-summarizer"
  role = aws_iam_role.summarizer_scheduler[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.summarizer[0].arn
    }]
  })
}

resource "aws_scheduler_schedule" "summarizer" {
  count = var.summaries.enable ? 1 : 0

  name                         = local.summarizer_name
  schedule_expression          = var.summaries.schedule
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.summarizer[0].arn
    role_arn = aws_iam_role.summarizer_scheduler[0].arn
    input    = "{}"
  }
}

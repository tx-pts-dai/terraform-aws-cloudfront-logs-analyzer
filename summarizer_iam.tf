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
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
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

  statement {
    sid       = "WriteRobotsFromUrl"
    actions   = ["s3:PutObject"]
    resources = ["${local.results_arn}/${local.robots_from_url_key}"]
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

locals {
  ua_lambda_name = "cloudfront-ua-preprocessing-${local.distribution_ids_name}"
  ua_lambda_src  = "${path.module}/preprocessing/user-agents"
}

# Dependencies Layer
# ?? Still deciding if this should be with terraform or installed/updated with a workflow. ??
resource "terraform_data" "ua_pip_install" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  triggers_replace = [filemd5("${local.ua_lambda_src}/requirements.txt")]

  provisioner "local-exec" {
    command = <<-CMD
      pip install \
        -r "${local.ua_lambda_src}/requirements.txt" \
        -t "${local.ua_lambda_src}/layer/python" \
        --platform manylinux2014_x86_64 \
        --python-version 3.12 \
        --only-binary=:all: \
        --upgrade --quiet
    CMD
  }
}

# Zipped layer
data "archive_file" "ua_layer" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  type        = "zip"
  source_dir  = "${local.ua_lambda_src}/layer"
  output_path = "${local.ua_lambda_src}/layer.zip"

  depends_on = [terraform_data.ua_pip_install]
}

resource "aws_lambda_layer_version" "ua_deps" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  filename                 = data.archive_file.ua_layer[0].output_path
  source_code_hash         = data.archive_file.ua_layer[0].output_base64sha256
  layer_name               = "${local.ua_lambda_name}-deps"
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
}

# Function zip
data "archive_file" "ua_lambda" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  type        = "zip"
  output_path = "${local.ua_lambda_src}/function.zip"

  source {
    content  = file("${local.ua_lambda_src}/pp-user-agent.py")
    filename = "pp_user_agent.py"
  }
}

# IAM
resource "aws_iam_role" "ua_lambda" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  name = local.ua_lambda_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = merge(var.tags, local.common_tags, { Name = local.ua_lambda_name })
}
resource "aws_iam_role_policy" "ua_lambda" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  name = local.ua_lambda_name
  role = aws_iam_role.ua_lambda[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.ua_lambda[0].arn}:*"]
      },
      {
        Sid      = "ReadSourceParquet"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["arn:aws:s3:::${var.s3_parquet_bucket.name}/${local.s3_parquet_bucket_sanitized_prefix}/*"]
      },
      {
        Sid      = "WriteOutput"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["arn:aws:s3:::${local.s3_preprocessing_bucket_name}/${local.s3_preprocessed_bucket_sanitized_prefix}/*"]
      },
      {
        Sid    = "ConsumeSQS"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = [aws_sqs_queue.ua_preprocessing[0].arn]
      },
    ]
  })
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "ua_lambda" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  name              = "/aws/lambda/${local.ua_lambda_name}"
  retention_in_days = 7

  tags = merge(var.tags, local.common_tags, { Name = local.ua_lambda_name })
}

# SQS: Dead-Letter Queue + Main Queue
resource "aws_sqs_queue" "ua_preprocessing_dlq" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  name                      = "${local.ua_lambda_name}-dlq"
  message_retention_seconds = 86400 * 2 # 2 days

  tags = merge(var.tags, local.common_tags, { Name = "${local.ua_lambda_name}-dlq" })
}

resource "aws_sqs_queue" "ua_preprocessing" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  name                       = local.ua_lambda_name
  visibility_timeout_seconds = 120   # must be ≥ Lambda timeout (60s)
  message_retention_seconds  = 86400 # 1 day

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ua_preprocessing_dlq[0].arn
    maxReceiveCount     = 3
  })

  tags = merge(var.tags, local.common_tags, { Name = local.ua_lambda_name })
}

# Allow the source S3 bucket to publish events to the SQS queue
resource "aws_sqs_queue_policy" "ua_preprocessing" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  queue_url = aws_sqs_queue.ua_preprocessing[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowS3SendMessage"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.ua_preprocessing[0].arn
      Condition = {
        ArnLike = {
          "aws:SourceArn" = "arn:aws:s3:::${var.s3_parquet_bucket.name}"
        }
      }
    }]
  })
}

# S3 Event Notification -> SQS
resource "aws_s3_bucket_notification" "ua_preprocessing" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  bucket = var.s3_parquet_bucket.name

  queue {
    id            = "ua-preprocessing"
    queue_arn     = aws_sqs_queue.ua_preprocessing[0].arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "${local.s3_parquet_bucket_sanitized_prefix}/"
    filter_suffix = ".parquet"
  }

  depends_on = [aws_sqs_queue_policy.ua_preprocessing]
}

# Lambda Function
resource "aws_lambda_function" "ua_preprocessing" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  function_name    = local.ua_lambda_name
  role             = aws_iam_role.ua_lambda[0].arn
  filename         = data.archive_file.ua_lambda[0].output_path
  source_code_hash = data.archive_file.ua_lambda[0].output_base64sha256
  handler          = "pp_user_agent.handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  timeout          = 60
  memory_size      = 512

  layers = [aws_lambda_layer_version.ua_deps[0].arn]

  environment {
    variables = {
      OUTPUT_BUCKET = local.s3_preprocessing_bucket_name
      OUTPUT_PREFIX = local.s3_preprocessed_bucket_sanitized_prefix
      INPUT_PREFIX  = local.s3_parquet_bucket_sanitized_prefix
    }
  }

  # Ensures the log group exists (and its retention policy) before the function first runs
  depends_on = [aws_cloudwatch_log_group.ua_lambda]

  tags = merge(var.tags, local.common_tags, { Name = local.ua_lambda_name })
}

# Lambda ESM: SQS -> Lambda
resource "aws_lambda_event_source_mapping" "ua_preprocessing" {
  count = var.user_agents_preprocessing.enable ? 1 : 0

  event_source_arn                   = aws_sqs_queue.ua_preprocessing[0].arn
  function_name                      = aws_lambda_function.ua_preprocessing[0].arn
  batch_size                         = 1 # one S3 file per Lambda invocation
  maximum_batching_window_in_seconds = 0
}

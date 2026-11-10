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
    input    = jsonencode({ scheduled_time = "<aws.scheduler.scheduled-time>" })
  }
}

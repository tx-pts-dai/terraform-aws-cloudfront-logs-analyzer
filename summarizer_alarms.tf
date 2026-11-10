resource "aws_cloudwatch_metric_alarm" "summarizer_errors" {
  count = var.summaries.enable ? 1 : 0

  alarm_name          = "${local.summarizer_name}-errors"
  alarm_description   = "Summarizer Lambda failed"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.summarizer[0].function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.summaries.alarm_actions
  ok_actions          = var.summaries.alarm_actions

  tags = merge(var.tags, local.common_tags, { Name = "${local.summarizer_name}-errors" })
}

resource "aws_cloudwatch_metric_alarm" "summarizer_not_running" {
  count = var.summaries.enable ? 1 : 0

  alarm_name          = "${local.summarizer_name}-not-running"
  alarm_description   = "Summarizer Lambda was not invoked for 2 hours"
  namespace           = "AWS/Lambda"
  metric_name         = "Invocations"
  dimensions          = { FunctionName = aws_lambda_function.summarizer[0].function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 2
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_actions       = var.summaries.alarm_actions
  ok_actions          = var.summaries.alarm_actions

  tags = merge(var.tags, local.common_tags, { Name = "${local.summarizer_name}-not-running" })
}

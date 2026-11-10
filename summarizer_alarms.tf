resource "aws_cloudwatch_metric_alarm" "summarizer_failed_events" {
  count = var.summaries.enable ? 1 : 0

  alarm_name          = "${local.summarizer_name}-failed-events"
  alarm_description   = "Summarizer events failed after all retries; the message in the failed-events queue holds the hour"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.summarizer_failed[0].name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.summaries.alarm_actions
  ok_actions          = var.summaries.alarm_actions

  tags = merge(var.tags, local.common_tags, { Name = "${local.summarizer_name}-failed-events" })
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

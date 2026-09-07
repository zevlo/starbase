resource "aws_sns_topic" "ops" {
  name = "${local.name_prefix}-ops"
}

resource "aws_sns_topic_subscription" "ops_email" {
  count     = var.alert_email == null ? 0 : 1
  topic_arn = aws_sns_topic.ops.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# The ingest function threw (not a handled LL2 error; those are metrics, not exceptions).
resource "aws_cloudwatch_metric_alarm" "ingest_errors" {
  alarm_name          = "${local.name_prefix}-ingest-errors"
  alarm_description   = "starbase-ingest Lambda reported unhandled errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.ingest.function_name }
  statistic           = "Sum"
  period              = 1800
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]
}

# No launches observed for 40 minutes = four missed 10-minute polls. Missing data
# is breaching on purpose: silence is the failure mode we care about.
resource "aws_cloudwatch_metric_alarm" "ingest_stale" {
  alarm_name          = "${local.name_prefix}-ingest-stale"
  alarm_description   = "starbase-ingest has not successfully observed any launches in 40 minutes"
  namespace           = "starbase/Ingest"
  metric_name         = "ItemsSeen"
  dimensions          = { Job = "upcoming" }
  statistic           = "Sum"
  period              = 2400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
  ok_actions          = [aws_sns_topic.ops.arn]
}

# Any 429 from LL2 means the budget math is wrong or the egress IP is shared. Worth knowing.
resource "aws_cloudwatch_metric_alarm" "ingest_rate_limited" {
  alarm_name          = "${local.name_prefix}-ingest-rate-limited"
  alarm_description   = "Launch Library 2 returned HTTP 429 to starbase-ingest"
  namespace           = "starbase/Ingest"
  metric_name         = "RateLimited"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.ops.arn]
}

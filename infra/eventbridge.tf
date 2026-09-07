# EventBridge schedules (cron is always UTC).
#   upcoming: :00 :10 :20 :30 :40 :50   -> 6 calls/hour
#   previous: :05 :35                   -> 2 calls/hour (optional)
# Both target retries are disabled so the schedule itself is the only retry.

resource "aws_cloudwatch_event_rule" "ingest_upcoming" {
  name                = "${local.name_prefix}-ingest-upcoming"
  description         = "Poll LL2 /launch/upcoming every 10 minutes"
  schedule_expression = "cron(0/10 * * * ? *)"
}

resource "aws_cloudwatch_event_target" "ingest_upcoming" {
  rule      = aws_cloudwatch_event_rule.ingest_upcoming.name
  target_id = "starbase-ingest"
  arn       = aws_lambda_function.ingest.arn
  input     = jsonencode({ job = "upcoming", limit = 20, mode = "detailed" })

  retry_policy {
    maximum_retry_attempts       = 0
    maximum_event_age_in_seconds = 60
  }
}

resource "aws_lambda_permission" "ingest_upcoming" {
  statement_id  = "AllowEventBridgeUpcoming"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ingest_upcoming.arn
}

resource "aws_cloudwatch_event_rule" "ingest_previous" {
  count               = var.enable_previous_ingest ? 1 : 0
  name                = "${local.name_prefix}-ingest-previous"
  description         = "Poll LL2 /launch/previous every 30 minutes, offset from the upcoming rule"
  schedule_expression = "cron(5/30 * * * ? *)"
}

resource "aws_cloudwatch_event_target" "ingest_previous" {
  count     = var.enable_previous_ingest ? 1 : 0
  rule      = aws_cloudwatch_event_rule.ingest_previous[0].name
  target_id = "starbase-ingest"
  arn       = aws_lambda_function.ingest.arn
  input     = jsonencode({ job = "previous", limit = 10, mode = "normal" })

  retry_policy {
    maximum_retry_attempts       = 0
    maximum_event_age_in_seconds = 60
  }
}

resource "aws_lambda_permission" "ingest_previous" {
  count         = var.enable_previous_ingest ? 1 : 0
  statement_id  = "AllowEventBridgePrevious"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ingest_previous[0].arn
}

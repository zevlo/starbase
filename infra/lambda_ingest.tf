# starbase-ingest: EventBridge -> Lambda -> LL2 -> DynamoDB

data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/ingest"
  output_path = "${path.module}/build/ingest.zip"
  excludes    = ["__pycache__", "local_run.py"]
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.name_prefix}-ingest"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest" {
  name               = "${local.name_prefix}-ingest-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "ingest" {
  statement {
    sid = "TableWrites"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:Query",
    ]
    resources = [aws_dynamodb_table.launches.arn]
  }

  statement {
    sid       = "IndexQueryForDemotion"
    actions   = ["dynamodb:Query"]
    resources = ["${aws_dynamodb_table.launches.arn}/index/gsi1-lane-net"]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }

  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["starbase/Ingest"]
    }
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "ingest"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

resource "aws_lambda_function" "ingest" {
  function_name    = "${local.name_prefix}-ingest"
  description      = "Polls Launch Library 2 on a schedule and upserts launch state into DynamoDB"
  role             = aws_iam_role.ingest.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "ingest_handler.lambda_handler"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  timeout          = 30
  memory_size      = 256

  # Never run two ingests at once; keeps the rate-limit proof simple.
  reserved_concurrent_executions = 1

  environment {
    variables = {
      TABLE_NAME       = aws_dynamodb_table.launches.name
      LL2_BASE_URL     = var.ll2_base_url
      USER_AGENT       = local.user_agent
      HOURLY_CALL_CAP  = tostring(var.hourly_call_cap)
      METRIC_NAMESPACE = "starbase/Ingest"
      LOG_LEVEL        = "INFO"
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.ingest.name
  }

  depends_on = [aws_iam_role_policy.ingest, aws_cloudwatch_log_group.ingest]
}

# Lambda would otherwise retry failed async invocations twice; that is a hidden
# multiplier on outbound LL2 calls, so it is disabled.
resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name                = aws_lambda_function.ingest.function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 60
}

# starbase-ask: API Gateway (POST /api/v1/ask) -> CAPCOM on AgentCore Runtime.
# Spends model tokens, so it is metered: the daily quota counter lives in the
# ask handler (ASK#CAPCOM / DAY#<utc-date>), WAF rate-limits the path, and the
# route itself is throttled in apigw.tf. Zip is built by `make ask-zip` because
# the bundled boto3 must be new enough to ship the bedrock-agentcore client.

resource "aws_cloudwatch_log_group" "ask" {
  name              = "/aws/lambda/${local.name_prefix}-ask"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "ask" {
  name               = "${local.name_prefix}-ask-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "ask" {
  statement {
    sid     = "InvokeCopilot"
    actions = ["bedrock-agentcore:InvokeAgentRuntime"]
    # The data plane evaluates against the runtime ENDPOINT arn
    # (.../runtime/<id>/runtime-endpoint/DEFAULT), not the runtime arn itself.
    resources = [
      aws_bedrockagentcore_agent_runtime.copilot.agent_runtime_arn,
      "${aws_bedrockagentcore_agent_runtime.copilot.agent_runtime_arn}/*",
    ]
  }

  statement {
    sid       = "QuotaCounter"
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.launches.arn]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ask.arn}:*"]
  }
}

resource "aws_iam_role_policy" "ask" {
  name   = "ask"
  role   = aws_iam_role.ask.id
  policy = data.aws_iam_policy_document.ask.json
}

resource "aws_lambda_function" "ask" {
  function_name    = "${local.name_prefix}-ask"
  description      = "POST /api/v1/ask -> CAPCOM (AgentCore Runtime), behind the daily quota"
  role             = aws_iam_role.ask.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "ask_handler.lambda_handler"
  filename         = "${path.module}/build/ask.zip" # `make ask-zip` before plan/apply
  source_code_hash = filemd5("${path.module}/build/ask.zip")
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      TABLE_NAME          = aws_dynamodb_table.launches.name
      AGENT_RUNTIME_ARN   = aws_bedrockagentcore_agent_runtime.copilot.agent_runtime_arn
      DAILY_LIMIT         = var.copilot_daily_limit
      LOG_LEVEL           = "INFO"
      RETRY_DELAY_SECONDS = "0.5"
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.ask.name
  }

  depends_on = [aws_iam_role_policy.ask, aws_cloudwatch_log_group.ask]
}

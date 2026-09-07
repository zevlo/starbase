# starbase-read: API Gateway -> Lambda -> DynamoDB (read-only)

data "archive_file" "read" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/read"
  output_path = "${path.module}/build/read.zip"
  excludes    = ["__pycache__"]
}

resource "aws_cloudwatch_log_group" "read" {
  name              = "/aws/lambda/${local.name_prefix}-read"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "read" {
  name               = "${local.name_prefix}-read-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "read" {
  statement {
    sid     = "TableReads"
    actions = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [
      aws_dynamodb_table.launches.arn,
      "${aws_dynamodb_table.launches.arn}/index/gsi1-lane-net",
    ]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.read.arn}:*"]
  }
}

resource "aws_iam_role_policy" "read" {
  name   = "read"
  role   = aws_iam_role.read.id
  policy = data.aws_iam_policy_document.read.json
}

resource "aws_lambda_function" "read" {
  function_name    = "${local.name_prefix}-read"
  description      = "Serves /api/v1/* from DynamoDB"
  role             = aws_iam_role.read.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "read_handler.lambda_handler"
  filename         = data.archive_file.read.output_path
  source_code_hash = data.archive_file.read.output_base64sha256
  timeout          = 10
  memory_size      = 256

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.launches.name
      LOG_LEVEL  = "INFO"
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.read.name
  }

  depends_on = [aws_iam_role_policy.read, aws_cloudwatch_log_group.read]
}

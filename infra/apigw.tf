# HTTP API (v2). In production the dashboard reaches this through CloudFront at
# https://starbase.zevlo.net/api/*, so browsers see a single origin and CORS is
# irrelevant. CORS below exists only for local frontend development.

resource "aws_cloudwatch_log_group" "apigw_access" {
  name              = "/starbase/apigw-access"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_api" "api" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"
  description   = "starbase read API"

  cors_configuration {
    allow_origins = ["http://localhost:8080", "http://localhost:5173", "http://127.0.0.1:8080"]
    allow_methods = ["GET"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_integration" "read" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.read.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

locals {
  api_routes = [
    "GET /api/v1/board",
    "GET /api/v1/launches",
    "GET /api/v1/launches/{id}",
    "GET /api/v1/health",
  ]
}

resource "aws_apigatewayv2_route" "read" {
  for_each  = toset(local.api_routes)
  api_id    = aws_apigatewayv2_api.api.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.read.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 20
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.apigw_access.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      method         = "$context.httpMethod"
      path           = "$context.path"
      status         = "$context.status"
      latency        = "$context.responseLatency"
      integrationErr = "$context.integrationErrorMessage"
    })
  }
}

resource "aws_lambda_permission" "apigw_read" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.read.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*/api/*"
}

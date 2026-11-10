output "table_name" {
  value = aws_dynamodb_table.launches.name
}

output "ingest_function_name" {
  value = aws_lambda_function.ingest.function_name
}

output "read_function_name" {
  value = aws_lambda_function.read.function_name
}

output "api_url" {
  description = "Direct API Gateway URL (the dashboard uses the CloudFront /api/* path instead)"
  value       = aws_apigatewayv2_api.api.api_endpoint
}

output "site_bucket" {
  value = aws_s3_bucket.site.bucket
}

output "cloudfront_id" {
  value = aws_cloudfront_distribution.cdn.id
}

output "cloudfront_url" {
  value = "https://${aws_cloudfront_distribution.cdn.domain_name}"
}

output "site_url" {
  value = "https://${var.site_domain}"
}

output "agent_api_url" {
  description = "AgentCore Gateway MCP endpoint: the read API as agent-callable tools"
  value       = aws_bedrockagentcore_gateway.agent_api.gateway_url
}

output "copilot_runtime_arn" {
  value = aws_bedrockagentcore_agent_runtime.copilot.agent_runtime_arn
}

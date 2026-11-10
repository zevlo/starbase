# Agent API: the public read API exposed as agent-callable tools through an
# AgentCore Gateway (MCP-compatible). Same four routes, same read path — the
# gateway is a second door, not a second API. Anonymous + rate-limited, like
# the public API itself.

data "aws_iam_policy_document" "agentcore_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "gateway" {
  name               = "${local.name_prefix}-gateway-role"
  assume_role_policy = data.aws_iam_policy_document.agentcore_trust.json
}

resource "aws_bedrockagentcore_gateway" "agent_api" {
  name        = "${local.name_prefix}-agent-api"
  description = "starbase read API as agent tools (board, launches, launch, health)"
  role_arn    = aws_iam_role.gateway.arn

  authorizer_type = "NONE" # keyless, like the public API; rate limits + WAF guard it
  protocol_type   = "MCP"

  protocol_configuration {
    mcp {
      instructions = "Tools over starbase's live rocket-launch data: the board (featured + upcoming launches), the launch list (upcoming lane holds at most 20; past lane keeps 30 days), single-launch detail, and pipeline health. Data refreshes from Launch Library 2 roughly every 10 minutes."
      search_type  = "SEMANTIC"
    }
  }
}

resource "aws_bedrockagentcore_gateway_target" "api" {
  name               = "starbase-api"
  gateway_identifier = aws_bedrockagentcore_gateway.agent_api.gateway_id
  description        = "starbase public read API (OpenAPI); every call rides the same path browsers use"

  # The upstream API Gateway endpoints are unauthenticated; the gateway
  # SigV4-signs with its own role (API GW ignores the signature on routes
  # with auth NONE). An explicit service name is required for openApiSchema
  # targets — an empty role block fails service-side validation.
  credential_provider_configuration {
    gateway_iam_role {
      service = "execute-api"
    }
  }

  target_configuration {
    mcp {
      open_api_schema {
        inline_payload {
          payload = templatefile("${path.module}/agentcore-openapi.json.tpl", {
            api_endpoint = aws_apigatewayv2_api.api.api_endpoint
          })
        }
      }
    }
  }
}

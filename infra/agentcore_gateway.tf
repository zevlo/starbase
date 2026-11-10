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

  # The API Gateway endpoint is unauthenticated; the empty role block satisfies
  # the required credential provider without SigV4-signing upstream calls.
  credential_provider_configuration {
    gateway_iam_role {}
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

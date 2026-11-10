# WAF in front of CloudFront, scoped to the one path that spends model tokens.
# A single scripted IP must not be able to burn the shared CAPCOM daily quota
# before real visitors wake up: >30 /api/v1/ask requests per IP per 5 minutes
# gets blocked. Board traffic is untouched; the DDB counter (in the ask
# handler) remains the global backstop against distributed abuse.

resource "aws_wafv2_web_acl" "ask_rate" {
  name  = "${local.name_prefix}-ask-rate"
  scope = "CLOUDFRONT" # must live in us-east-1, which is where this stack runs

  default_action {
    allow {}
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name_prefix}-ask-rate"
    sampled_requests_enabled   = true
  }

  rule {
    name     = "ask-rate-per-ip"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 30 # requests per 5-minute window, per IP
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            positional_constraint = "STARTS_WITH"
            search_string         = "/api/v1/ask"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name_prefix}-ask-rate-rule"
      sampled_requests_enabled   = true
    }
  }

  tags = {}
}

resource "aws_wafv2_web_acl_association" "cdn" {
  resource_arn = aws_cloudfront_distribution.cdn.arn
  web_acl_arn  = aws_wafv2_web_acl.ask_rate.arn
}

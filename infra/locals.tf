data "aws_caller_identity" "current" {}

locals {
  name_prefix = "starbase"
  account_id  = data.aws_caller_identity.current.account_id
  table_name  = "${local.name_prefix}-launches"
  user_agent  = "starbase/1.0 (+${var.github_repo_url})"
}

output "state_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}

output "gha_plan_role_arn" {
  value = aws_iam_role.gha_plan.arn
}

output "gha_apply_role_arn" {
  value = aws_iam_role.gha_apply.arn
}

output "oidc_provider_arn" {
  value = data.aws_iam_openid_connect_provider.github.arn
}

# One-time bootstrap. Run locally with an admin profile; state is local (gitignored).
# Creates: Terraform remote-state bucket, GitHub OIDC provider, and the two CI roles.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "starbase"
      ManagedBy = "terraform-bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  state_bucket = "starbase-tfstate-${local.account_id}"

  # GitHub issues "immutable" subject claims that embed the owner and repository
  # numeric IDs (repo:owner@<id>/name@<id>:...), which survive renames and cannot
  # be spoofed by re-creating a deleted repo. Match that form, and the plain form
  # for repos still on the legacy template.
  repo_subs = [
    "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}",
    "repo:${var.github_owner}/${var.github_repo}",
  ]
  plan_subs  = flatten([for s in local.repo_subs : ["${s}:pull_request", "${s}:ref:refs/heads/*"]])
  apply_subs = flatten([for s in local.repo_subs : ["${s}:ref:refs/heads/main", "${s}:environment:prod"]])
}

# ---------------------------------------------------------------------------
# Remote state bucket
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "tfstate" {
  bucket = local.state_bucket

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# GitHub OIDC provider (account-wide, pre-existing; shared with other repos,
# so it is referenced rather than managed here)
# ---------------------------------------------------------------------------
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

# ---------------------------------------------------------------------------
# Plan role: PRs and non-main branches. Read-only + state bucket access.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "gha_plan_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.plan_subs
    }
  }
}

resource "aws_iam_role" "gha_plan" {
  name                 = "starbase-gha-plan"
  assume_role_policy   = data.aws_iam_policy_document.gha_plan_trust.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "gha_plan_readonly" {
  role       = aws_iam_role.gha_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "gha_plan_state" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket", "s3:GetBucketVersioning"]
    resources = [aws_s3_bucket.tfstate.arn]
  }
  statement {
    sid       = "StateObjectsAndLockfile"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/*"]
  }
}

resource "aws_iam_role_policy" "gha_plan_state" {
  name   = "terraform-state"
  role   = aws_iam_role.gha_plan.id
  policy = data.aws_iam_policy_document.gha_plan_state.json
}

# ---------------------------------------------------------------------------
# Apply role: main branch only. Scoped to starbase-* resources.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "gha_apply_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.apply_subs
    }
  }
}

resource "aws_iam_role" "gha_apply" {
  name                 = "starbase-gha-apply"
  assume_role_policy   = data.aws_iam_policy_document.gha_apply_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "gha_apply" {
  statement {
    sid       = "State"
    actions   = ["s3:ListBucket", "s3:GetBucketVersioning", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]
  }

  statement {
    sid       = "Identity"
    actions   = ["sts:GetCallerIdentity"]
    resources = ["*"]
  }

  statement {
    sid     = "DynamoDB"
    actions = ["dynamodb:*"]
    resources = [
      "arn:aws:dynamodb:${var.region}:${local.account_id}:table/starbase-*",
    ]
  }

  statement {
    sid     = "Lambda"
    actions = ["lambda:*"]
    resources = [
      "arn:aws:lambda:${var.region}:${local.account_id}:function:starbase-*",
    ]
  }

  statement {
    sid     = "EventBridge"
    actions = ["events:*"]
    resources = [
      "arn:aws:events:${var.region}:${local.account_id}:rule/starbase-*",
    ]
  }

  statement {
    sid       = "ApiGateway"
    actions   = ["apigateway:*"]
    resources = ["arn:aws:apigateway:${var.region}::/*"]
  }

  statement {
    sid       = "CloudFront"
    actions   = ["cloudfront:*"]
    resources = ["*"]
  }

  statement {
    sid     = "S3Buckets"
    actions = ["s3:*"]
    resources = [
      "arn:aws:s3:::starbase-*",
      "arn:aws:s3:::starbase-*/*",
    ]
  }

  statement {
    sid       = "LogsDescribe"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }

  statement {
    sid     = "Logs"
    actions = ["logs:*"]
    resources = [
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/starbase-*",
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/starbase-*:*",
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/starbase/*",
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/starbase/*:*",
    ]
  }

  statement {
    sid = "IamRoles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:UpdateRole",
      "iam:UpdateRoleDescription", "iam:UpdateAssumeRolePolicy",
      "iam:TagRole", "iam:UntagRole", "iam:ListRoleTags",
      "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies",
      "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/starbase-*"]
  }

  statement {
    sid       = "IamPassRoleToLambda"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/starbase-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }

  # The CI roles must not be able to modify themselves or each other.
  statement {
    sid     = "DenySelfEscalation"
    effect  = "Deny"
    actions = ["iam:*"]
    resources = [
      "arn:aws:iam::${local.account_id}:role/starbase-gha-*",
    ]
  }

  statement {
    sid       = "CloudWatchAlarms"
    actions   = ["cloudwatch:*"]
    resources = ["arn:aws:cloudwatch:${var.region}:${local.account_id}:alarm:starbase-*"]
  }

  statement {
    sid       = "Sns"
    actions   = ["sns:*"]
    resources = ["arn:aws:sns:${var.region}:${local.account_id}:starbase-*"]
  }

  # DNS: only this hosted zone, only records under the starbase subdomain.
  statement {
    sid = "Route53ZoneRead"
    actions = [
      "route53:GetHostedZone",
      "route53:ListResourceRecordSets",
      "route53:ListTagsForResource",
    ]
    resources = ["arn:aws:route53:::hostedzone/${var.hosted_zone_id}"]
  }

  statement {
    sid       = "Route53ZoneWriteScoped"
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = ["arn:aws:route53:::hostedzone/${var.hosted_zone_id}"]
    condition {
      test     = "ForAllValues:StringLike"
      variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
      values = [
        var.site_domain,
        "*.${var.site_domain}",
      ]
    }
  }

  statement {
    sid       = "Route53Global"
    actions   = ["route53:GetChange", "route53:ListHostedZones", "route53:ListHostedZonesByName"]
    resources = ["*"]
  }

  statement {
    sid = "Acm"
    actions = [
      "acm:RequestCertificate", "acm:DescribeCertificate", "acm:ListCertificates",
      "acm:AddTagsToCertificate", "acm:ListTagsForCertificate", "acm:RemoveTagsFromCertificate",
      "acm:GetCertificate",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "AcmDeleteTaggedOnly"
    actions   = ["acm:DeleteCertificate"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = ["starbase"]
    }
  }
}

resource "aws_iam_role_policy" "gha_apply" {
  name   = "starbase-apply"
  role   = aws_iam_role.gha_apply.id
  policy = data.aws_iam_policy_document.gha_apply.json
}

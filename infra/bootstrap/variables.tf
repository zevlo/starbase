variable "region" {
  type    = string
  default = "us-east-1"
}

variable "github_owner" {
  type        = string
  description = "GitHub user/org that owns the repo"
}

variable "github_repo" {
  type    = string
  default = "starbase"
}

variable "github_owner_id" {
  type        = number
  description = "Numeric GitHub user/org id (gh api users/<owner> --jq .id); embedded in immutable OIDC subjects"
}

variable "github_repo_id" {
  type        = number
  description = "Numeric GitHub repository id (gh api repos/<owner>/<repo> --jq .id)"
}

variable "hosted_zone_id" {
  type        = string
  description = "Existing Route53 hosted zone the CI apply role may write starbase records into"
}

variable "site_domain" {
  type    = string
  default = "starbase.zevlo.net"
}

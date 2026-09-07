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

variable "hosted_zone_id" {
  type        = string
  description = "Existing Route53 hosted zone the CI apply role may write starbase records into"
}

variable "site_domain" {
  type    = string
  default = "starbase.zevlo.net"
}

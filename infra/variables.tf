variable "region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "ll2_base_url" {
  type        = string
  description = "Launch Library 2 base URL, including API version, no trailing slash"
  default     = "https://ll.thespacedevs.com/2.2.0"

  validation {
    condition     = !can(regex("lldev", var.ll2_base_url)) || var.environment == "dev"
    error_message = "lldev.thespacedevs.com serves stale data and must not be used outside environment = dev."
  }

  validation {
    condition     = !endswith(var.ll2_base_url, "/")
    error_message = "ll2_base_url must not end with a slash."
  }
}

variable "hourly_call_cap" {
  type        = number
  description = "Hard cap on outbound LL2 calls per UTC hour, enforced by a DynamoDB counter. LL2 free tier is 15/hour/IP."
  default     = 12

  validation {
    condition     = var.hourly_call_cap >= 1 && var.hourly_call_cap <= 14
    error_message = "hourly_call_cap must stay strictly below LL2's 15/hour limit."
  }
}

variable "enable_previous_ingest" {
  type        = bool
  description = "Also poll /launch/previous/ every 30 minutes (2 calls/hour)"
  default     = true
}

variable "site_domain" {
  type    = string
  default = "starbase.zevlo.net"
}

variable "hosted_zone_name" {
  type        = string
  description = "Existing public Route53 zone that site_domain lives under"
  default     = "zevlo.net"
}

variable "acm_certificate_arn" {
  type        = string
  description = "Optional: reuse an existing us-east-1 ACM cert instead of issuing one"
  default     = null
}

variable "alert_email" {
  type        = string
  description = "Optional email for CloudWatch alarm notifications (SNS subscription must be confirmed manually)"
  default     = null
}

variable "github_repo_url" {
  type        = string
  description = "Used in the outbound User-Agent so LL2 operators can identify this client"
  default     = "https://github.com/zevlo/starbase"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

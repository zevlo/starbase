# starbase.zevlo.net lives in a pre-existing zone that other projects also use.
# The zone is read, never managed, here. The CI apply role can only write records
# under the starbase subdomain (see infra/bootstrap).

data "aws_route53_zone" "site" {
  name         = var.hosted_zone_name
  private_zone = false
}

locals {
  issue_certificate = var.acm_certificate_arn == null
}

# CloudFront requires the certificate to live in us-east-1, which is this stack's region.
resource "aws_acm_certificate" "site" {
  count             = local.issue_certificate ? 1 : 0
  domain_name       = var.site_domain
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "site_cert_validation" {
  for_each = local.issue_certificate ? {
    for dvo in aws_acm_certificate.site[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  zone_id         = data.aws_route53_zone.site.zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 300
  records         = [each.value.record]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "site" {
  count                   = local.issue_certificate ? 1 : 0
  certificate_arn         = aws_acm_certificate.site[0].arn
  validation_record_fqdns = [for r in aws_route53_record.site_cert_validation : r.fqdn]

  timeouts {
    create = "15m"
  }
}

locals {
  certificate_arn = local.issue_certificate ? aws_acm_certificate_validation.site[0].certificate_arn : var.acm_certificate_arn
}

resource "aws_route53_record" "site_a" {
  zone_id = data.aws_route53_zone.site.zone_id
  name    = var.site_domain
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.cdn.domain_name
    zone_id                = aws_cloudfront_distribution.cdn.hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "site_aaaa" {
  zone_id = data.aws_route53_zone.site.zone_id
  name    = var.site_domain
  type    = "AAAA"

  alias {
    name                   = aws_cloudfront_distribution.cdn.domain_name
    zone_id                = aws_cloudfront_distribution.cdn.hosted_zone_id
    evaluate_target_health = false
  }
}

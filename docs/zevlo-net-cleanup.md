# zevlo.net cleanup plan (pending decision)

Date: 2026-11-06. The starbase AWS stack was fully torn down on this date
(main stack + bootstrap: CloudFront, API GW, Lambdas, DynamoDB, S3, EventBridge,
IAM CI roles, state bucket). This file documents what's left in the account
that is related only to the `zevlo.net` domain, so the decision can be made later.

## Current state (verified 2026-11-06)

Hosted zone `Z065948732114TULELWJE` (`zevlo.net`) contains only:

| Record | Type | Target | Status |
|---|---|---|---|
| `zevlo.net` | A (alias) | `d382nhm9isyzkx.cloudfront.net` | **dangling** — that CloudFront distribution no longer exists; the domain does not resolve |
| `www.zevlo.net` | A (alias) | `d382nhm9isyzkx.cloudfront.net` | **dangling** — same |
| `_cc53769365ab20988a2ce8ef3138c92c.zevlo.net` | CNAME | `...acm-validations.aws` | ACM DNS validation for a cert below; harmless |
| `zevlo.net` | NS / SOA | — | boilerplate |

Also in the account (us-east-1, `746669194590`):

- ACM cert `7a2c1ccc-bb4c-4981-a1c8-47d7d524013d` — `zevlo.net` + `*.zevlo.net`, ISSUED, **unused** (`InUseBy: []`), expires 2027-02-12
- ACM cert `5dc7fa83-ee47-4ad1-ad77-ce46de79d1f2` — `zevlo.net` + `*.zevlo.net`, ISSUED, **unused**, expires 2027-02-14

Domain registration (separate from the zone, unaffected by zone deletion):

- Registrar: Amazon Registrar, Inc.
- Expires: 2027-01-14, **auto-renew: ON**

## Costs today

| Item | Cost |
|---|---|
| Route53 hosted zone | **$0.50/month** — the only real charge |
| ACM public certs (unused) | $0 |
| Domain registration | ~$10–12/yr, next charge 2027-01-14 if auto-renew stays on |

## Options

### Option A — keep everything (do nothing)

Current burn: $0.50/mo + ~$12/yr from Jan 2027. Zone and registration stay warm,
so any future project (or a starbase resurrection) can use the domain immediately.

### Option B — delete the dead zone, keep the registration (saves $0.50/mo)

Deletes the dangling records, unused certs, and the hosted zone. The domain stays
registered and can get a fresh zone later. Steps:

```bash
ZONE=Z065948732114TULELWJE
ACCOUNT=746669194590

# 1. Delete the three non-boilerplate records (Route53 refuses to delete a
#    zone that still has any). One change batch:
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE" --change-batch '{
  "Changes": [
    {"Action":"DELETE","ResourceRecordSet":{"Name":"zevlo.net.","Type":"A","AliasTarget":{"HostedZoneId":"Z2FDTNDATAQYW2","DNSName":"d382nhm9isyzkx.cloudfront.net.","EvaluateTargetHealth":false}}},
    {"Action":"DELETE","ResourceRecordSet":{"Name":"www.zevlo.net.","Type":"A","AliasTarget":{"HostedZoneId":"Z2FDTNDATAQYW2","DNSName":"d382nhm9isyzkx.cloudfront.net.","EvaluateTargetHealth":false}}},
    {"Action":"DELETE","ResourceRecordSet":{"Name":"_cc53769365ab20988a2ce8ef3138c92c.zevlo.net.","Type":"CNAME","TTL":60,"ResourceRecords":[{"Value":"_731467f0927c437f78103670cfe57e8d.jkddzztszm.acm-validations.aws."}]}}
  ]
}'

# 2. Delete the two unused ACM certs:
aws acm delete-certificate --certificate-arn "arn:aws:acm:us-east-1:${ACCOUNT}:certificate/7a2c1ccc-bb4c-4981-a1c8-47d7d524013d"
aws acm delete-certificate --certificate-arn "arn:aws:acm:us-east-1:${ACCOUNT}:certificate/5dc7fa83-ee47-4ad1-ad77-ce46de79d1f2"

# 3. Delete the (now empty) zone:
aws route53 delete-hosted-zone --id "$ZONE"

# 4. Verify:
aws route53 list-hosted-zones --query 'HostedZones[].Name' --output text   # zevlo.net gone
aws acm list-certificates --output text                                    # empty
```

Caveat: certs are free, so step 2 is optional tidiness — but if the certs are
kept and the zone deleted, they become un-renewable via DNS validation.

### Option C — Option B, plus let the domain lapse (saves $0.50/mo + ~$12/yr)

```bash
aws route53domains disable-domain-auto-renewal --domain-name zevlo.net
```

The domain then expires 2027-01-14. **Warning:** after expiry + grace period
(~45 days redemption), the name is released to the public and anyone can
register it. Only do this if `zevlo.net` is truly never coming back.

## Resurrection notes (if starbase returns)

- `infra/bootstrap/terraform.tfvars` pins `hosted_zone_id = Z065948732114TULELWJE`.
  With Option B/C the zone is gone: create a new zone for the domain and update
  that var (and `aws_route53_zone.site` in `infra/dns.tf` looks it up by name, so
  only the bootstrap var needs the new ID).
- Redeploy path: `cd infra/bootstrap && terraform apply` → re-add the push
  trigger in `.github/workflows/terraform.yml` (disarmed in commit 26f2fec) →
  manual workflow run.
- Pre-teardown state backup: `backup/terraform.tfstate.pre-teardown`.

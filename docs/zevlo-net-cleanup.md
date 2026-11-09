# zevlo.net cleanup — resolved

**Outcome (2026-11-09): starbase is back up; Option A + tidy.** The hosted zone
and registration were kept, the dead records and unused certs were deleted.

## What happened

- **2026-11-06:** starbase AWS stack fully torn down (main + bootstrap). This
  file originally documented the leftover-domain decision (keep zone / delete
  zone / let domain lapse).
- **2026-11-09:** stack resurrected end to end:
  - bootstrap re-applied (state bucket + CI roles; apply role gained the
    account-level `logs:CreateLogDelivery` permissions that API GW access
    logging needs), destroy guards restored
  - GitHub Actions repo variables recreated (`AWS_PLAN_ROLE_ARN`,
    `AWS_APPLY_ROLE_ARN`, `SITE_BUCKET`, `CLOUDFRONT_ID`), CI triggers re-armed
  - full stack re-created via the terraform workflow (43 resources), site
    redeployed, ingest re-seeded — https://starbase.zevlo.net live again
  - dangling `zevlo.net` / `www.zevlo.net` alias records and the two unused
    `zevlo.net` + `*.zevlo.net` ACM certs deleted

## Current domain state

- Hosted zone `Z065948732114TULELWJE` (`zevlo.net`): NS/SOA boilerplate plus
  the Terraform-managed `starbase.zevlo.net` A/AAAA aliases and cert-validation
  CNAME. Delegation matches the registrar (Amazon Registrar, auto-renew ON,
  expires 2027-01-14).
- ACM: only the active `starbase.zevlo.net` certificate.

## If this happens again

- The zone survives stack teardowns: leave it (it is the only ~$0.50/mo cost),
  and `infra/bootstrap/terraform.tfvars` keeps working as-is.
- Resurrection runbook: bootstrap apply → recreate repo-level Actions vars →
  merge (triggers apply) → set `CLOUDFRONT_ID` from `terraform output` → run
  the site workflow. Pre-teardown state backup of the 2026-11-06 teardown:
  `backup/terraform.tfstate.pre-teardown`.

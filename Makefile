PY        ?= .venv/bin/python
PYTEST    ?= .venv/bin/pytest
RUFF      ?= .venv/bin/ruff
TABLE     ?= starbase-launches
LLDEV     ?= https://lldev.thespacedevs.com/2.2.0
API_URL   ?= $(shell cd infra && terraform output -raw api_url 2>/dev/null)
BUCKET    ?= $(shell cd infra && terraform output -raw site_bucket 2>/dev/null)
DIST_ID   ?= $(shell cd infra && terraform output -raw cloudfront_id 2>/dev/null)

.PHONY: venv test lint fmt ingest-local ingest-fixture fixtures mocks web plan apply deploy-site invoke logs poke-hold

venv:
	python3 -m venv .venv && .venv/bin/pip install -q pytest "moto[dynamodb]" boto3 ruff

test:
	$(PYTEST)

lint:
	$(RUFF) check lambdas scripts && $(RUFF) format --check lambdas scripts && node --check web/app.js
	cd infra && terraform fmt -check -recursive

fmt:
	$(RUFF) check --fix lambdas scripts && $(RUFF) format lambdas scripts
	cd infra && terraform fmt -recursive

## Run the ingest handler locally against the unlimited dev mirror (stale data, no rate limit).
ingest-local:
	LL2_BASE_URL=$(LLDEV) TABLE_NAME=$(TABLE) $(PY) lambdas/ingest/local_run.py --job upcoming

## Run the ingest handler with a file fixture (e.g. FIXTURE=fixtures/hold.json).
ingest-fixture:
	FIXTURE_FILE=$(FIXTURE) TABLE_NAME=$(TABLE) $(PY) lambdas/ingest/local_run.py --job upcoming

## Regenerate Hold / In Flight fixtures from the captured LL2 payload.
fixtures:
	$(PY) scripts/make_fixture.py --status Hold --net=+2m > fixtures/hold.json
	$(PY) scripts/make_fixture.py --status "In Flight" --net=-30s > fixtures/in_flight.json

## Regenerate web/mock/*.json from the live API.
mocks:
	$(PY) scripts/make_mocks.py --api $(API_URL)

## Serve the dashboard locally. Try http://localhost:8080/?mock=hold or ?api=$(API_URL)
web:
	cd web && python3 -m http.server 8080

plan:
	cd infra && terraform plan

apply:
	cd infra && terraform apply

## Manual site deploy (CI does this on push to main).
deploy-site:
	aws s3 sync web/ s3://$(BUCKET)/ --exclude index.html --cache-control "public, max-age=3600, stale-while-revalidate=86400" --delete
	aws s3 cp web/index.html s3://$(BUCKET)/index.html --cache-control "public, max-age=60" --content-type "text/html; charset=utf-8"
	aws cloudfront create-invalidation --distribution-id $(DIST_ID) --paths "/" "/index.html" "/app.js" "/styles.css" "/mock/*" --query 'Invalidation.Id' --output text

## Invoke the deployed ingest once (consumes 1 LL2 call from the hourly budget).
invoke:
	aws lambda invoke --function-name starbase-ingest --cli-binary-format raw-in-base64-out --payload '{"job":"upcoming"}' /dev/stdout

logs:
	aws logs tail /aws/lambda/starbase-ingest --since 1h --format short

## Demo: force the hero launch into HOLD. ll_last_updated is rewound so the next
## scheduled ingest (<= 10 min) sees "newer upstream data" and restores the truth from LL2.
poke-hold:
	@ID=$$(curl -s "$(API_URL)/api/v1/board?limit=1" | $(PY) -c 'import sys,json; print(json.load(sys.stdin)["hero"]["id"])'); \
	echo "holding $$ID"; \
	aws dynamodb update-item --table-name $(TABLE) \
	  --key "{\"pk\":{\"S\":\"LAUNCH#$$ID\"},\"sk\":{\"S\":\"META\"}}" \
	  --update-expression "SET status_abbrev = :s, status_name = :n, holdreason = :r, ll_last_updated = :old" \
	  --expression-attribute-values '{":s":{"S":"Hold"},":n":{"S":"On Hold"},":r":{"S":"Demo hold from the CLI"},":old":{"S":"2000-01-01T00:00:00Z"}}'

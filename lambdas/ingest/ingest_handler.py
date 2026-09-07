"""starbase-ingest: poll Launch Library 2 on an EventBridge schedule and upsert DynamoDB.

Event payload (constant JSON from the EventBridge target):
    {"job": "upcoming" | "previous", "limit": 20, "mode": "detailed" | "normal"}
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import boto3

import ll2
import mapping
from store import Store

LOG = logging.getLogger("starbase.ingest")
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ.get("TABLE_NAME", "starbase-launches")
LL2_BASE_URL = os.environ.get("LL2_BASE_URL", "https://lldev.thespacedevs.com/2.2.0")
USER_AGENT = os.environ.get("USER_AGENT", "starbase/1.0 (+https://github.com/zevlo/starbase)")
HOURLY_CALL_CAP = int(os.environ.get("HOURLY_CALL_CAP", "12"))
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "starbase/Ingest")
FIXTURE_FILE = os.environ.get("FIXTURE_FILE")  # local/dev only: read JSON from disk instead of HTTP
RETRY_DELAY_SECONDS = float(os.environ.get("RETRY_DELAY_SECONDS", "2"))

DEFAULTS = {
    "upcoming": {"limit": 20, "mode": "detailed"},
    "previous": {"limit": 10, "mode": "normal"},
}

_store: Store | None = None
_cloudwatch = None


def _get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(TABLE_NAME)
    return _store


def log(event: str, **fields: Any) -> None:
    LOG.info(json.dumps({"event": event, **fields}, default=str))


class Metrics:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def inc(self, name: str, value: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + value

    def flush(self, job: str) -> None:
        global _cloudwatch
        if os.environ.get("DISABLE_METRICS") == "1" or not self.counts:
            return
        if _cloudwatch is None:
            _cloudwatch = boto3.client("cloudwatch")
        data = []
        for name, value in self.counts.items():
            data.append({"MetricName": name, "Value": value, "Unit": "Count"})
            data.append(
                {
                    "MetricName": name,
                    "Value": value,
                    "Unit": "Count",
                    "Dimensions": [{"Name": "Job", "Value": job}],
                }
            )
        try:
            _cloudwatch.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=data)
        except Exception as exc:  # metrics must never fail the ingest
            LOG.warning("put_metric_data failed: %s", exc)


def _load_payload(url: str) -> dict:
    if FIXTURE_FILE:
        with open(FIXTURE_FILE, encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload.get("results"), list):
            raise ll2.FetchError("fixture missing results[]", retryable=False)
        return payload
    return ll2.fetch(url, USER_AGENT)


def fetch_with_budget(
    store: Store, url: str, now: datetime, metrics: Metrics
) -> tuple[dict | None, int | None, str | None]:
    """At most two attempts, each gated by the hourly budget. Never retries a 429."""
    last_error: str | None = None
    http_status: int | None = None
    for attempt in (1, 2):
        if not store.reserve_call(now, HOURLY_CALL_CAP):
            metrics.inc("BudgetSkips")
            log("budget_exhausted", cap=HOURLY_CALL_CAP, attempt=attempt)
            return None, http_status, "budget_exhausted"
        metrics.inc("LL2Calls")
        try:
            payload = _load_payload(url)
            return payload, 200, None
        except ll2.RateLimited as exc:
            metrics.inc("RateLimited")
            log("rate_limited", retry_after=exc.retry_after, attempt=attempt)
            return None, 429, "rate_limited"
        except ll2.FetchError as exc:
            metrics.inc("FetchErrors")
            last_error, http_status = str(exc), exc.status
            log("fetch_error", error=last_error, status=exc.status, retryable=exc.retryable, attempt=attempt)
            if not exc.retryable or attempt == 2:
                break
            time.sleep(RETRY_DELAY_SECONDS)
    return None, http_status, last_error


def lambda_handler(event: dict, context: Any = None) -> dict:
    job = (event or {}).get("job", "upcoming")
    if job not in DEFAULTS:
        raise ValueError(f"unknown job {job!r}")
    limit = max(1, min(int((event or {}).get("limit", DEFAULTS[job]["limit"])), 50))
    mode = (event or {}).get("mode", DEFAULTS[job]["mode"])
    now = datetime.now(UTC)
    started = time.monotonic()
    store = _get_store()
    metrics = Metrics()
    url = ll2.build_url(LL2_BASE_URL, job, limit, mode)

    log("ingest_start", job=job, url=url if not FIXTURE_FILE else f"fixture:{FIXTURE_FILE}")

    payload, http_status, error = fetch_with_budget(store, url, now, metrics)
    if payload is None:
        store.put_run_marker(
            job,
            now,
            ok=False,
            last_http_status=http_status,
            last_error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
            source_host=_host(url),
        )
        metrics.flush(job)
        log("ingest_failed", job=job, error=error, status=http_status)
        return {"job": job, "ok": False, "error": error, "status": http_status}

    results = payload.get("results", [])
    metrics.inc("ItemsSeen", len(results))
    counts = {"written": 0, "touched": 0, "skipped": 0, "invalid": 0, "status_changes": 0, "demoted": 0}
    fetched_ids: set[str] = set()

    for raw in results:
        try:
            item = mapping.to_item(raw, now)
        except mapping.MappingError as exc:
            counts["invalid"] += 1
            log("invalid_launch", error=str(exc))
            continue
        if item.get("status_raw_abbrev"):
            log("unknown_status", id=item["id"], abbrev=item["status_raw_abbrev"])
        fetched_ids.add(item["id"])
        existing = store.get_launch(item["id"])
        outcome = store.upsert_launch(item, existing, now)
        counts[outcome] += 1
        if outcome == "written" and existing and existing.get("status_abbrev") != item["status_abbrev"]:
            counts["status_changes"] += 1
            log(
                "status_change",
                id=item["id"],
                name=item["name"],
                from_status=existing.get("status_abbrev"),
                to_status=item["status_abbrev"],
                net=item["net"],
            )
        if outcome == "written" and existing and existing.get("net") and existing.get("net") != item["net"]:
            log("net_change", id=item["id"], name=item["name"], from_net=existing.get("net"), to_net=item["net"])

    if job == "upcoming":
        cutoff = now - mapping.UPCOMING_GRACE
        for it in store.query_lane(mapping.LANE_UPCOMING):
            if it.get("id") in fetched_ids:
                continue
            try:
                net = mapping.parse_iso(it["net"])
            except (KeyError, ValueError):
                continue
            if net < cutoff:
                store.demote(it["id"], now)
                counts["demoted"] += 1
                log("demoted", id=it["id"], name=it.get("name"), net=it["net"])

    metrics.inc("UpsertedItems", counts["written"])
    metrics.inc("StatusChanges", counts["status_changes"])
    duration_ms = int((time.monotonic() - started) * 1000)
    store.put_run_marker(
        job,
        now,
        ok=True,
        last_http_status=200,
        last_error=None,
        items_seen=len(results),
        items_written=counts["written"],
        items_touched=counts["touched"],
        items_demoted=counts["demoted"],
        duration_ms=duration_ms,
        source_host=_host(url),
    )
    metrics.flush(job)
    result = {"job": job, "ok": True, "fetched": len(results), **counts, "duration_ms": duration_ms}
    log("ingest_done", **result)
    return result


def _host(url: str) -> str:
    try:
        return url.split("/")[2]
    except IndexError:
        return url

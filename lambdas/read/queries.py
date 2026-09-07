"""Read-side DynamoDB queries and public JSON shaping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

INDEX = "gsi1-lane-net"
TERMINAL = {"Success", "Failure", "Partial Failure"}
UPCOMING_GRACE = timedelta(hours=6)
STALE_AFTER_SECONDS = 1800

SOURCE = {
    "name": "Launch Library 2",
    "by": "The Space Devs",
    "url": "https://thespacedevs.com/llapi",
}


def iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def public_launch(item: dict) -> dict:
    it = _plain(item)
    return {
        "id": it.get("id"),
        "name": it.get("name"),
        "slug": it.get("slug"),
        "net": it.get("net"),
        "net_precision": it.get("net_precision"),
        "window_start": it.get("window_start"),
        "window_end": it.get("window_end"),
        "status": {"abbrev": it.get("status_abbrev"), "name": it.get("status_name")},
        "prev_status": it.get("prev_status_abbrev"),
        "provider": it.get("provider_name"),
        "vehicle": it.get("vehicle_name"),
        "pad": {
            "name": it.get("pad_name"),
            "location": it.get("pad_location"),
            "lat": it.get("pad_lat"),
            "lon": it.get("pad_lon"),
        },
        "mission": {
            "name": it.get("mission_name"),
            "type": it.get("mission_type"),
            "description": it.get("mission_description"),
            "orbit": {"abbrev": it.get("orbit_abbrev"), "name": it.get("orbit_name")},
        },
        "image_url": it.get("image_url"),
        "webcast_live": bool(it.get("webcast_live", False)),
        "webcast_url": it.get("webcast_url"),
        "holdreason": it.get("holdreason"),
        "failreason": it.get("failreason"),
        "lane": it.get("lane"),
        "last_seen_at": it.get("last_seen_at"),
        "last_changed_at": it.get("last_changed_at"),
        "ll_last_updated": it.get("ll_last_updated"),
    }


class Reader:
    def __init__(self, table_name: str, resource: Any | None = None):
        self.table = (resource or boto3.resource("dynamodb")).Table(table_name)

    def upcoming(self, now: datetime, limit: int) -> list[dict]:
        since = iso_z(now - UPCOMING_GRACE)
        resp = self.table.query(
            IndexName=INDEX,
            KeyConditionExpression=Key("gsi1pk").eq("LANE#UPCOMING") & Key("gsi1sk").gte(since),
            ScanIndexForward=True,
            Limit=limit,
        )
        return resp.get("Items", [])

    def past(self, limit: int) -> list[dict]:
        resp = self.table.query(
            IndexName=INDEX,
            KeyConditionExpression=Key("gsi1pk").eq("LANE#PAST"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return resp.get("Items", [])

    def launch(self, launch_id: str) -> dict | None:
        return self.table.get_item(Key={"pk": f"LAUNCH#{launch_id}", "sk": "META"}).get("Item")

    def run_markers(self) -> dict[str, dict]:
        resp = self.table.query(KeyConditionExpression=Key("pk").eq("META#INGEST"))
        return {i["job"]: _plain(i) for i in resp.get("Items", []) if "job" in i}

    # -- composite views ----------------------------------------------------
    def board(self, now: datetime, strip_limit: int) -> dict:
        # Over-fetch a little; terminal statuses cannot be in this lane but stay defensive.
        items = [i for i in self.upcoming(now, strip_limit + 6) if i.get("status_abbrev") not in TERMINAL]
        hero: dict | None = None
        for it in items:
            if it.get("status_abbrev") == "In Flight":
                hero = it
                break
        if hero is None and items:
            hero = items[0]
        strip = [i for i in items if hero is None or i["id"] != hero["id"]][:strip_limit]
        return {
            "generated_at": iso_z(now),
            "source": SOURCE,
            "ingest": self.ingest_summary(now),
            "hero": public_launch(hero) if hero else None,
            "strip": [public_launch(i) for i in strip],
        }

    def ingest_summary(self, now: datetime) -> dict:
        markers = self.run_markers()
        up = markers.get("upcoming", {})
        last_ok = up.get("last_ok_at")
        stale = int((now - parse_iso(last_ok)).total_seconds()) if last_ok else None
        return {
            "last_ok_at": last_ok,
            "last_run_at": up.get("last_run_at"),
            "last_error": up.get("last_error"),
            "stale_seconds": stale,
            "stale": stale is None or stale > STALE_AFTER_SECONDS,
            "source_host": up.get("source_host"),
        }

    def health(self, now: datetime) -> tuple[int, dict]:
        markers = self.run_markers()
        summary = self.ingest_summary(now)
        body = {
            "status": "degraded" if summary["stale"] else "ok",
            "generated_at": iso_z(now),
            "ingest": summary,
            "jobs": markers,
            "source": SOURCE,
        }
        return (503 if summary["stale"] else 200), body

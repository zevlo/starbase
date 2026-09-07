"""DynamoDB access for the ingest pipeline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from mapping import LANE_PAST, iso_z

# Attributes that the upsert must never overwrite with the incoming value.
_PROTECTED = {"pk", "sk", "first_seen_at", "prev_status_abbrev", "last_changed_at"}

EXISTING_PROJECTION = "id, ll_last_updated, status_abbrev, lane, net, #n"


class Store:
    def __init__(self, table_name: str, resource: Any | None = None):
        self._ddb = resource or boto3.resource("dynamodb")
        self.table = self._ddb.Table(table_name)

    # -- rate budget -------------------------------------------------------
    def reserve_call(self, now: datetime, cap: int) -> bool:
        """Atomically consume one outbound-call token for the current UTC hour.

        Returns False (and consumes nothing) once `cap` calls have been reserved
        in this hour. This is the hard guarantee that keeps us under LL2's limit
        no matter how many times the function is invoked.
        """
        hour_start = now.replace(minute=0, second=0, microsecond=0)
        try:
            self.table.update_item(
                Key={"pk": "RATE#LL2", "sk": f"HOUR#{hour_start.strftime('%Y-%m-%dT%H')}"},
                UpdateExpression="ADD calls :one SET expires_at = :exp, updated_at = :now",
                ConditionExpression="attribute_not_exists(calls) OR calls < :cap",
                ExpressionAttributeValues={
                    ":one": 1,
                    ":cap": cap,
                    ":exp": int((hour_start + timedelta(hours=2)).timestamp()),
                    ":now": iso_z(now),
                },
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    # -- launches ----------------------------------------------------------
    def get_launch(self, launch_id: str) -> dict | None:
        resp = self.table.get_item(
            Key={"pk": f"LAUNCH#{launch_id}", "sk": "META"},
            ProjectionExpression=EXISTING_PROJECTION,
            ExpressionAttributeNames={"#n": "name"},
        )
        return resp.get("Item")

    def touch(self, launch_id: str, now: datetime) -> None:
        self.table.update_item(
            Key={"pk": f"LAUNCH#{launch_id}", "sk": "META"},
            UpdateExpression="SET last_seen_at = :now",
            ExpressionAttributeValues={":now": iso_z(now)},
        )

    def upsert_launch(self, item: dict, existing: dict | None, now: datetime) -> str:
        """Idempotent conditional upsert. Returns 'written', 'touched' or 'skipped'."""
        if (
            existing
            and existing.get("ll_last_updated") == item.get("ll_last_updated")
            and existing.get("lane") == item.get("lane")
        ):
            self.touch(item["id"], now)
            return "touched"

        names: dict[str, str] = {}
        values: dict[str, Any] = {":now": iso_z(now), ":llu": item["ll_last_updated"]}
        sets: list[str] = []
        for i, (key, value) in enumerate(item.items()):
            if key in _PROTECTED:
                continue
            names[f"#a{i}"] = key
            values[f":v{i}"] = value
            sets.append(f"#a{i} = :v{i}")

        prev_status = (existing or {}).get("status_abbrev") or item["status_abbrev"]
        values[":prev"] = prev_status
        sets.append("first_seen_at = if_not_exists(first_seen_at, :now)")
        sets.append("prev_status_abbrev = :prev")
        sets.append("last_changed_at = :now")

        try:
            self.table.update_item(
                Key={"pk": item["pk"], "sk": item["sk"]},
                UpdateExpression="SET " + ", ".join(sets),
                # Never let a slow/out-of-order invocation regress to older upstream data.
                ConditionExpression="attribute_not_exists(ll_last_updated) OR ll_last_updated <= :llu",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return "written"
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return "skipped"
            raise

    def query_lane(self, lane: str, since: str | None = None, limit: int | None = None) -> list[dict]:
        cond = Key("gsi1pk").eq(f"LANE#{lane}")
        if since:
            cond = cond & Key("gsi1sk").gte(since)
        kwargs: dict[str, Any] = {"IndexName": "gsi1-lane-net", "KeyConditionExpression": cond}
        if limit:
            kwargs["Limit"] = limit
        items: list[dict] = []
        while True:
            resp = self.table.query(**kwargs)
            items.extend(resp.get("Items", []))
            if limit and len(items) >= limit:
                return items[:limit]
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                return items
            kwargs["ExclusiveStartKey"] = lek

    def demote(self, launch_id: str, now: datetime) -> None:
        self.table.update_item(
            Key={"pk": f"LAUNCH#{launch_id}", "sk": "META"},
            UpdateExpression="SET gsi1pk = :lane_pk, lane = :lane, last_changed_at = :now",
            ExpressionAttributeValues={
                ":lane_pk": f"LANE#{LANE_PAST}",
                ":lane": LANE_PAST,
                ":now": iso_z(now),
            },
        )

    # -- run marker --------------------------------------------------------
    def put_run_marker(self, job: str, now: datetime, ok: bool, **fields: Any) -> None:
        item: dict[str, Any] = {
            "pk": "META#INGEST",
            "sk": f"JOB#{job}",
            "job": job,
            "last_run_at": iso_z(now),
            "ok": ok,
        }
        item.update({k: v for k, v in fields.items() if v is not None})
        if ok:
            item["last_ok_at"] = iso_z(now)
        else:
            # Preserve the previous last_ok_at so /health can compute staleness.
            prev = self.table.get_item(
                Key={"pk": "META#INGEST", "sk": f"JOB#{job}"}, ProjectionExpression="last_ok_at"
            ).get("Item", {})
            if prev.get("last_ok_at"):
                item["last_ok_at"] = prev["last_ok_at"]
        self.table.put_item(Item=item)


def utcnow() -> datetime:
    return datetime.now(UTC)

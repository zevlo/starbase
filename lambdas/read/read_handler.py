"""starbase-read: API Gateway (HTTP API, payload v2.0) -> DynamoDB.

Routes (all GET):
    /api/v1/board?limit=8
    /api/v1/launches?lane=upcoming|past&limit=20
    /api/v1/launches/{id}
    /api/v1/health
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from queries import Reader, public_launch

LOG = logging.getLogger("starbase.read")
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ.get("TABLE_NAME", "starbase-launches")
DATA_SOURCE_HEADER = "Launch Library 2 (The Space Devs)"

_reader: Reader | None = None
_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _get_reader() -> Reader:
    global _reader
    if _reader is None:
        _reader = Reader(TABLE_NAME)
    return _reader


def _response(status: int, body: dict, max_age: int = 30) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": f"public, max-age={max_age}",
            "X-Data-Source": DATA_SOURCE_HEADER,
        },
        "body": json.dumps(body, separators=(",", ":")),
    }


def _error(status: int, code: str, message: str) -> dict:
    return _response(status, {"error": code, "message": message}, max_age=0)


def _int_param(params: dict, name: str, default: int, lo: int, hi: int) -> int:
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(lo, min(int(raw), hi))
    except ValueError:
        return default


def lambda_handler(event: dict, context: Any = None) -> dict:
    method = (event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or "/").rstrip("/") or "/"
    params = event.get("queryStringParameters") or {}
    path_params = event.get("pathParameters") or {}
    now = datetime.now(UTC)
    reader = _get_reader()

    if method != "GET":
        return _error(405, "method_not_allowed", "Only GET is supported")

    try:
        if path == "/api/v1/board":
            limit = _int_param(params, "limit", 8, 1, 20)
            return _response(200, reader.board(now, limit))

        if path == "/api/v1/launches":
            lane = (params.get("lane") or "upcoming").lower()
            limit = _int_param(params, "limit", 20, 1, 50)
            if lane == "upcoming":
                items = reader.upcoming(now, limit)
            elif lane == "past":
                items = reader.past(limit)
            else:
                return _error(400, "bad_lane", "lane must be 'upcoming' or 'past'")
            return _response(
                200,
                {
                    "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "lane": lane,
                    "count": len(items),
                    "launches": [public_launch(i) for i in items],
                },
            )

        if path.startswith("/api/v1/launches/"):
            launch_id = path_params.get("id") or path.rsplit("/", 1)[-1]
            if not _ID_RE.match(launch_id):
                return _error(400, "bad_id", "malformed launch id")
            item = reader.launch(launch_id)
            if not item:
                return _error(404, "not_found", f"no launch {launch_id}")
            return _response(200, {"launch": public_launch(item)})

        if path == "/api/v1/health":
            status, body = reader.health(now)
            return _response(status, body, max_age=10)

        return _error(404, "not_found", f"no route for {path}")
    except Exception:  # pragma: no cover - defensive; API GW would otherwise return an opaque 500
        LOG.exception("unhandled error on %s", path)
        return _error(500, "internal", "internal error")

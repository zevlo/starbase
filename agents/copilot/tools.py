"""CAPCOM's eyes: read tools over the starbase table.

Thin wrappers around the same Reader the public read API uses, so the copilot
and the board can never disagree about the data. No agent-framework imports
here on purpose: this module stays unit-testable with just boto3.

Knowledge boundary (docs/adr/0001): everything CAPCOM knows arrives through
these four functions. There is no tool that can leave the table.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from queries import Reader, public_launch

MAX_LIMIT = 20

_reader: Reader | None = None


def _get_reader() -> Reader:
    global _reader
    if _reader is None:
        _reader = Reader(os.environ.get("TABLE_NAME", "starbase-launches"))
    return _reader


def _clamp_limit(limit: int) -> int:
    try:
        return max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        return MAX_LIMIT


def get_board(limit: int = 8) -> dict:
    """The board the dashboard shows: the hero launch plus the upcoming strip.

    Use for "what's the next launch" / "what's happening now" questions. The
    ingest block reports how fresh the data is (stale means > 30 min old).
    """
    return _get_reader().board(datetime.now(UTC), _clamp_limit(limit))


def list_launches(lane: str = "upcoming", limit: int = MAX_LIMIT) -> dict:
    """List launches stored by starbase, newest-first within a lane.

    lane="upcoming": the next launches (at most 20 are stored). lane="past":
    flights that already happened — only the last 30 days are retained.
    """
    lane = str(lane).lower().strip()
    if lane not in ("upcoming", "past"):
        lane = "upcoming"
    reader = _get_reader()
    now = datetime.now(UTC)
    items = reader.upcoming(now, _clamp_limit(limit)) if lane == "upcoming" else reader.past(_clamp_limit(limit))
    return {"lane": lane, "count": len(items), "launches": [public_launch(i) for i in items]}


def get_launch(launch_id: str) -> dict:
    """One launch by id, with every stored detail: mission, pad, window, hold
    reason, fail reason, webcast link."""
    item = _get_reader().launch(str(launch_id))
    if not item:
        return {"error": "not_found", "message": f"starbase has no launch {launch_id}"}
    return {"launch": public_launch(item)}


def get_health() -> dict:
    """Pipeline health: when ingest last succeeded and whether data is stale."""
    _status, body = _get_reader().health(datetime.now(UTC))
    return body

"""Map a Launch Library 2 launch object to a starbase DynamoDB item.

Tolerates both the 2.2.0 shape (image is a string, vidURLs) and the 2.3.0
shape (image is an object with image_url, vid_urls).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

TERMINAL_STATUSES = {"Success", "Failure", "Partial Failure"}
KNOWN_STATUSES = {"Go", "TBD", "TBC", "Hold", "In Flight"} | TERMINAL_STATUSES
LANE_UPCOMING = "UPCOMING"
LANE_PAST = "PAST"

# A launch whose NET has passed stays in the upcoming lane for this long so the
# dashboard keeps showing it while LL2 catches up with the outcome.
UPCOMING_GRACE = timedelta(hours=6)
EXPIRES_AFTER = timedelta(days=30)
DESCRIPTION_MAX = 500


class MappingError(ValueError):
    pass


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (LL2 uses trailing Z) into an aware UTC datetime."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(obj: Any, *path: str, default: Any = None) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def pick_image_url(raw: dict) -> str | None:
    image = raw.get("image")
    if isinstance(image, str) and image:
        return image
    if isinstance(image, dict):
        url = image.get("image_url") or image.get("url")
        return url or None
    return None


def pick_webcast_url(raw: dict) -> str | None:
    """Highest-priority video URL, or None. Only present with mode=detailed on 2.2.0."""
    vids = raw.get("vidURLs") or raw.get("vid_urls") or []
    best: tuple[int, str] | None = None
    for v in vids:
        if not isinstance(v, dict):
            continue
        url = v.get("url")
        if not url:
            continue
        prio = v.get("priority")
        prio = int(prio) if isinstance(prio, (int, float)) else -1
        if best is None or prio > best[0]:
            best = (prio, url)
    return best[1] if best else None


def normalize_status(abbrev: Any) -> str:
    if isinstance(abbrev, str) and abbrev in KNOWN_STATUSES:
        return abbrev
    return "TBD"


def compute_lane(status_abbrev: str, net: datetime, now: datetime) -> str:
    if status_abbrev in TERMINAL_STATUSES:
        return LANE_PAST
    if status_abbrev == "In Flight":
        return LANE_UPCOMING
    if net >= now - UPCOMING_GRACE:
        return LANE_UPCOMING
    return LANE_PAST


def to_item(raw: dict, now: datetime) -> dict:
    launch_id = raw.get("id")
    net_raw = raw.get("net")
    if not isinstance(launch_id, str) or not launch_id:
        raise MappingError("launch missing id")
    if not isinstance(net_raw, str) or not net_raw:
        raise MappingError(f"launch {launch_id} missing net")
    try:
        net = parse_iso(net_raw)
    except ValueError as exc:
        raise MappingError(f"launch {launch_id} bad net {net_raw!r}") from exc

    raw_abbrev = _get(raw, "status", "abbrev")
    status_abbrev = normalize_status(raw_abbrev)
    lane = compute_lane(status_abbrev, net, now)
    net_iso = iso_z(net)

    item: dict[str, Any] = {
        "pk": f"LAUNCH#{launch_id}",
        "sk": "META",
        "gsi1pk": f"LANE#{lane}",
        "gsi1sk": f"{net_iso}#{launch_id}",
        "lane": lane,
        "id": launch_id,
        "name": raw.get("name") or launch_id,
        "slug": raw.get("slug"),
        "net": net_iso,
        "net_precision": _get(raw, "net_precision", "abbrev"),
        "window_start": raw.get("window_start"),
        "window_end": raw.get("window_end"),
        "status_id": _get(raw, "status", "id"),
        "status_name": _get(raw, "status", "name") or status_abbrev,
        "status_abbrev": status_abbrev,
        "status_raw_abbrev": raw_abbrev if raw_abbrev != status_abbrev else None,
        "provider_name": _get(raw, "launch_service_provider", "name"),
        "vehicle_name": _get(raw, "rocket", "configuration", "full_name")
        or _get(raw, "rocket", "configuration", "name"),
        "pad_name": _get(raw, "pad", "name"),
        "pad_location": _get(raw, "pad", "location", "name"),
        "pad_lat": _get(raw, "pad", "latitude"),
        "pad_lon": _get(raw, "pad", "longitude"),
        "mission_name": _get(raw, "mission", "name"),
        "mission_type": _get(raw, "mission", "type"),
        "mission_description": _truncate(_get(raw, "mission", "description")),
        "orbit_abbrev": _get(raw, "mission", "orbit", "abbrev"),
        "orbit_name": _get(raw, "mission", "orbit", "name"),
        "image_url": pick_image_url(raw),
        "webcast_live": bool(raw.get("webcast_live", False)),
        "webcast_url": pick_webcast_url(raw),
        "holdreason": raw.get("holdreason") or None,
        "failreason": raw.get("failreason") or None,
        "ll_last_updated": raw.get("last_updated") or net_iso,
        "last_seen_at": iso_z(now),
        "expires_at": int((net + EXPIRES_AFTER).timestamp()),
    }
    # Keep items lean; DynamoDB stores NULL otherwise.
    return {k: v for k, v in item.items() if v is not None}


def _truncate(text: Any) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    if len(text) <= DESCRIPTION_MAX:
        return text
    return text[: DESCRIPTION_MAX - 1].rstrip() + "…"

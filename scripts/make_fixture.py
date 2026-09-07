#!/usr/bin/env python3
"""Derive a Hold / In Flight / Go fixture from the captured LL2 payload.

    python scripts/make_fixture.py --status Hold --net=+2m > fixtures/hold.json
    python scripts/make_fixture.py --status "In Flight" --net=-30s > fixtures/in_flight.json

(Use --net=VALUE; a bare "-30s" is parsed as a flag.)

The first result is rewritten with the requested status and a NET relative to now,
its last_updated is bumped so the ingest treats it as fresh, and all other results
are shifted into the future so they land in the UPCOMING lane.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

STATUS = {
    "Go": {
        "id": 1,
        "name": "Go for Launch",
        "abbrev": "Go",
        "description": "Current T-0 confirmed by official or reliable sources.",
    },
    "TBD": {
        "id": 2,
        "name": "To Be Determined",
        "abbrev": "TBD",
        "description": "Current date is a placeholder or rough estimation based on unreliable or interpreted sources.",
    },
    "Success": {
        "id": 3,
        "name": "Launch Successful",
        "abbrev": "Success",
        "description": "The launch vehicle successfully inserted its payload(s) into the target orbit(s).",
    },
    "Failure": {
        "id": 4,
        "name": "Launch Failure",
        "abbrev": "Failure",
        "description": "Either the launch vehicle did not reach orbit, or the payload(s) failed to separate.",
    },
    "Hold": {
        "id": 5,
        "name": "On Hold",
        "abbrev": "Hold",
        "description": "The countdown has been paused, but the launch can still happen within the launch window.",
    },
    "In Flight": {
        "id": 6,
        "name": "Launch in Flight",
        "abbrev": "In Flight",
        "description": "The launch vehicle has lifted off from the pad.",
    },
    "TBC": {
        "id": 8,
        "name": "To Be Confirmed",
        "abbrev": "TBC",
        "description": "Awaiting official confirmation - current date is known with some certainty.",
    },
}


def parse_offset(text: str) -> timedelta:
    m = re.fullmatch(r"([+-]?)(\d+)([smhd])", text)
    if not m:
        raise SystemExit(f"bad offset {text!r}; use e.g. +2m, -30s, +3h, +1d")
    sign = -1 if m.group(1) == "-" else 1
    n = int(m.group(2))
    unit = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[m.group(3)]
    return sign * timedelta(**{unit: n})


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="fixtures/ll2_upcoming_2.2.0.json")
    ap.add_argument("--status", choices=sorted(STATUS), default="Hold")
    ap.add_argument("--net", default="+2m", help="NET offset from now for the first launch")
    ap.add_argument("--holdreason", default="Range violation; awaiting clearance from the RSO.")
    ap.add_argument("--webcast-live", action="store_true")
    args = ap.parse_args()

    now = datetime.now(UTC).replace(microsecond=0)
    payload = json.loads(Path(args.source).read_text(encoding="utf-8"))
    results = payload["results"]

    hero = results[0]
    hero["status"] = STATUS[args.status]
    hero["net"] = iso(now + parse_offset(args.net))
    hero["window_start"] = iso(now + parse_offset(args.net) - timedelta(hours=1))
    hero["window_end"] = iso(now + parse_offset(args.net) + timedelta(hours=2))
    hero["net_precision"] = {"id": 0, "name": "Second", "abbrev": "SEC", "description": ""}
    hero["holdreason"] = args.holdreason if args.status == "Hold" else ""
    hero["webcast_live"] = bool(args.webcast_live or args.status == "In Flight")
    hero["last_updated"] = iso(now)

    for i, launch in enumerate(results[1:], start=1):
        shift = timedelta(hours=6 * i)
        launch["net"] = iso(now + shift)
        launch["window_start"] = iso(now + shift - timedelta(minutes=30))
        launch["window_end"] = iso(now + shift + timedelta(hours=2))
        if launch["status"]["abbrev"] in {"Success", "Failure", "Partial Failure"}:
            launch["status"] = STATUS["Go"]
        launch["last_updated"] = iso(now)

    json.dump(payload, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

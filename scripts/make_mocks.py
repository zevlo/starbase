#!/usr/bin/env python3
"""Generate web/mock/board-{go,hold,inflight}.json from a real /api/v1/board response.

    python scripts/make_mocks.py --api https://<api-id>.execute-api.us-east-1.amazonaws.com
    python scripts/make_mocks.py --source /tmp/board.json

The dashboard rebases all timestamps at load time (see rebaseMock in web/app.js),
so the absolute NETs stored here do not matter.
"""

from __future__ import annotations

import argparse
import copy
import json
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "web" / "mock"

STATUS = {
    "Go": {"abbrev": "Go", "name": "Go for Launch"},
    "Hold": {"abbrev": "Hold", "name": "On Hold"},
    "In Flight": {"abbrev": "In Flight", "name": "Launch in Flight"},
}


def load(args) -> dict:
    if args.source:
        return json.loads(Path(args.source).read_text(encoding="utf-8"))
    req = urllib.request.Request(
        f"{args.api.rstrip('/')}/api/v1/board?limit=8", headers={"User-Agent": "starbase-make-mocks"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return json.load(resp)


def variant(board: dict, status: str, **hero_overrides) -> dict:
    b = copy.deepcopy(board)
    b["mock"] = {"variant": status}
    hero = b["hero"]
    hero["status"] = STATUS[status]
    hero["net_precision"] = "SEC"
    for k, v in hero_overrides.items():
        hero[k] = v
    return b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api")
    ap.add_argument("--source")
    args = ap.parse_args()
    if not (args.api or args.source):
        ap.error("--api or --source required")

    board = load(args)
    if not board.get("hero"):
        raise SystemExit("board has no hero; cannot build mocks")

    OUT.mkdir(parents=True, exist_ok=True)
    variants = {
        "go": variant(board, "Go", holdreason=None, webcast_live=False),
        "hold": variant(board, "Hold", holdreason="Range violation; awaiting clearance from the RSO."),
        "inflight": variant(
            board,
            "In Flight",
            webcast_live=True,
            webcast_url=board["hero"].get("webcast_url") or "https://www.youtube.com/@SpaceX",
        ),
    }
    for name, data in variants.items():
        path = OUT / f"board-{name}.json"
        path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        print("wrote", path.relative_to(OUT.parents[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

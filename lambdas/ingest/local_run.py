"""Run the ingest handler from a laptop.

    LL2_BASE_URL=https://lldev.thespacedevs.com/2.2.0 TABLE_NAME=starbase-launches \
        python lambdas/ingest/local_run.py --job upcoming

    FIXTURE_FILE=fixtures/hold.json python lambdas/ingest/local_run.py --job upcoming
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", choices=["upcoming", "previous"], default="upcoming")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--mode", choices=["normal", "detailed"])
    args = parser.parse_args()

    os.environ.setdefault("DISABLE_METRICS", "1")
    import ingest_handler  # noqa: E402  (after env is set)

    event = {"job": args.job}
    if args.limit:
        event["limit"] = args.limit
    if args.mode:
        event["mode"] = args.mode
    result = ingest_handler.lambda_handler(event, None)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

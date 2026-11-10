"""starbase-ask: API Gateway (HTTP API, payload v2.0) -> CAPCOM on AgentCore Runtime.

Route:
    POST /api/v1/ask   {"question": str, "session_id": uuid}

This is the only route that spends model tokens, so it is metered like the
pipeline meters LL2: a conditional DynamoDB counter (ASK#CAPCOM /
DAY#<utc-date>) hard-caps questions per UTC day. When the quota trips, CAPCOM
goes quiet for everyone until midnight UTC; the rest of the site is
unaffected. WAF and the per-route throttle sit in front (see infra/waf.tf,
infra/apigw.tf).
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError

LOG = logging.getLogger("starbase.ask")
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ.get("TABLE_NAME", "starbase-launches")
AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "300"))
RETRY_DELAY = float(os.environ.get("RETRY_DELAY_SECONDS", "0.5"))

# Fail cleanly before the platform kills us: ask Lambda runs at 30 s.
_INVOKE_TIMEOUT = 25
_MAX_QUESTION_CHARS = 500
DATA_SOURCE_HEADER = "Launch Library 2 (The Space Devs)"

_BOTOCORE = Config(connect_timeout=3, read_timeout=_INVOKE_TIMEOUT, retries={"max_attempts": 0})


class _Busy(Exception):
    """The runtime throttled us even after one retry."""


def _response(status: int, body: dict, max_age: int | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store" if max_age is None else f"public, max-age={max_age}",
            "X-Data-Source": DATA_SOURCE_HEADER,
        },
        "body": json.dumps(body, separators=(",", ":")),
    }


def _error(status: int, code: str, message: str) -> dict:
    return _response(status, {"error": code, "message": message})


def _validate(body: Any) -> tuple[str, str] | None:
    """Return (question, session_id) or None when the request is malformed."""
    if not isinstance(body, dict):
        return None
    question = body.get("question")
    if not isinstance(question, str):
        return None
    question = question.strip()
    if not question or len(question) > _MAX_QUESTION_CHARS:
        return None
    raw_session = body.get("session_id")
    if not isinstance(raw_session, str):
        return None
    try:
        session_id = str(uuid.UUID(raw_session))
    except ValueError:
        return None
    return question, session_id


def reserve_question(table: Any, now: datetime, cap: int) -> bool:
    """Atomically consume one CAPCOM question for the current UTC day.

    Mirrors Store.reserve_call (the LL2 hourly budget): a refused reservation
    consumes nothing, and the counter survives redeploys. Quota resets at
    UTC midnight with the key.
    """
    try:
        table.update_item(
            Key={"pk": "ASK#CAPCOM", "sk": f"DAY#{now.strftime('%Y-%m-%d')}"},
            UpdateExpression="ADD calls :one SET expires_at = :exp, updated_at = :now",
            ConditionExpression="attribute_not_exists(calls) OR calls < :cap",
            ExpressionAttributeValues={
                ":one": 1,
                ":cap": cap,
                ":exp": int((now + timedelta(days=2)).timestamp()),
                ":now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _answer_from(raw: Any) -> str:
    data = raw.read() if hasattr(raw, "read") else raw
    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
            return parsed["answer"]
    except (ValueError, TypeError, UnicodeDecodeError):
        pass
    return data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)


def _invoke(question: str, session_id: str, client: Any) -> str:
    payload = json.dumps({"question": question, "session_id": session_id}).encode()
    for attempt in (1, 2):
        try:
            resp = client.invoke_agent_runtime(
                agentRuntimeArn=AGENT_RUNTIME_ARN,
                runtimeSessionId=session_id,
                contentType="application/json",
                accept="application/json",
                payload=payload,
            )
            return _answer_from(resp["response"])
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ThrottlingException" and attempt == 1:
                time.sleep(RETRY_DELAY)
                continue
            if code in ("ThrottlingException", "ServiceUnavailableException", "GatewayTimeoutException"):
                raise _Busy() from exc
            raise
    raise _Busy()


def lambda_handler(event: dict, context: Any = None) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return _error(400, "bad_request", "body must be JSON")
    if event.get("requestContext", {}).get("http", {}).get("method", "POST").upper() != "POST":
        return _error(405, "method_not_allowed", "Only POST is supported")

    parsed = _validate(body)
    if parsed is None:
        return _error(400, "bad_request", "supply question (1-500 chars) and session_id (uuid)")
    question, session_id = parsed

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    if not reserve_question(table, datetime.now(UTC), DAILY_LIMIT):
        return _error(429, "daily_quota", "CAPCOM's daily quota is used up. Back at 00:00 UTC.")

    try:
        client = boto3.client("bedrock-agentcore", config=_BOTOCORE)
        answer = _invoke(question, session_id, client)
        return _response(200, {"answer": answer, "session_id": session_id})
    except _Busy:
        return _error(503, "capcom_busy", "CAPCOM is busy; try again shortly.")
    except ReadTimeoutError:
        return _error(503, "capcom_timeout", "CAPCOM timed out.")
    except Exception:
        LOG.exception("runtime invoke failed")
        return _error(502, "capcom_unreachable", "CAPCOM is unreachable.")

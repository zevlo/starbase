"""Minimal Launch Library 2 HTTP client. Standard library only."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT = 8.0

JOB_PATHS = {"upcoming": "launch/upcoming/", "previous": "launch/previous/"}


class FetchError(Exception):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class RateLimited(FetchError):
    def __init__(self, retry_after: str | None):
        super().__init__("LL2 returned 429", status=429, retryable=False)
        self.retry_after = retry_after


def build_url(base_url: str, job: str, limit: int, mode: str) -> str:
    if job not in JOB_PATHS:
        raise ValueError(f"unknown job {job!r}")
    query = urllib.parse.urlencode({"limit": limit, "mode": mode, "format": "json"})
    return f"{base_url.rstrip('/')}/{JOB_PATHS[job]}?{query}"


def fetch(url: str, user_agent: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https only, fixed host
            body = resp.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimited(exc.headers.get("Retry-After")) from exc
        retryable = 500 <= exc.code < 600
        raise FetchError(f"LL2 HTTP {exc.code}", status=exc.code, retryable=retryable) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise FetchError(f"LL2 network error: {exc}", retryable=True) from exc

    if len(body) > MAX_BODY_BYTES:
        raise FetchError("LL2 response exceeded 2 MiB", retryable=False)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise FetchError("LL2 response was not JSON", retryable=True) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise FetchError("LL2 response missing results[]", retryable=False)
    return payload

import io
import json
import urllib.error
from unittest import mock

import pytest

import ll2


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, headers: dict | None = None):
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, io.BytesIO(b""))


def test_build_url():
    url = ll2.build_url("https://ll.thespacedevs.com/2.2.0", "upcoming", 20, "detailed")
    assert url == "https://ll.thespacedevs.com/2.2.0/launch/upcoming/?limit=20&mode=detailed&format=json"
    assert "launch/previous/" in ll2.build_url("https://x/", "previous", 10, "normal")
    with pytest.raises(ValueError):
        ll2.build_url("https://x", "everything", 1, "normal")


def test_fetch_ok():
    body = json.dumps({"results": [{"id": "a"}]}).encode()
    with mock.patch("urllib.request.urlopen", return_value=_Resp(body)) as m:
        payload = ll2.fetch("https://x", "ua/1")
    assert payload["results"][0]["id"] == "a"
    req = m.call_args.args[0]
    assert req.get_header("User-agent") == "ua/1"


def test_fetch_429_is_rate_limited_not_retryable():
    with mock.patch("urllib.request.urlopen", side_effect=_http_error(429, {"Retry-After": "120"})):
        with pytest.raises(ll2.RateLimited) as ei:
            ll2.fetch("https://x", "ua")
    assert ei.value.retry_after == "120"
    assert ei.value.retryable is False


def test_fetch_5xx_retryable_4xx_not():
    with mock.patch("urllib.request.urlopen", side_effect=_http_error(503)):
        with pytest.raises(ll2.FetchError) as ei:
            ll2.fetch("https://x", "ua")
    assert ei.value.retryable is True and ei.value.status == 503

    with mock.patch("urllib.request.urlopen", side_effect=_http_error(404)):
        with pytest.raises(ll2.FetchError) as ei:
            ll2.fetch("https://x", "ua")
    assert ei.value.retryable is False and ei.value.status == 404


def test_fetch_timeout_retryable():
    with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        with pytest.raises(ll2.FetchError) as ei:
            ll2.fetch("https://x", "ua")
    assert ei.value.retryable is True


def test_fetch_rejects_non_json_and_bad_shape():
    with mock.patch("urllib.request.urlopen", return_value=_Resp(b"<html>")):
        with pytest.raises(ll2.FetchError):
            ll2.fetch("https://x", "ua")
    with mock.patch("urllib.request.urlopen", return_value=_Resp(b'{"nope": 1}')):
        with pytest.raises(ll2.FetchError):
            ll2.fetch("https://x", "ua")


def test_fetch_rejects_oversized_body():
    big = b"[" + b"1," * (ll2.MAX_BODY_BYTES // 2) + b"1]"
    with mock.patch("urllib.request.urlopen", return_value=_Resp(big)):
        with pytest.raises(ll2.FetchError):
            ll2.fetch("https://x", "ua")

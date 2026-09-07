import importlib
import json
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from conftest import FIXTURES
from moto import mock_aws

TABLE = "starbase-launches-test"


def _create_table():
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    return ddb.create_table(
        TableName=TABLE,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "gsi1pk", "AttributeType": "S"},
            {"AttributeName": "gsi1sk", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "gsi1-lane-net",
                "KeySchema": [
                    {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                    {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def _write_fixture(tmp_path, results, hero_status=None, hero_net_offset=None):
    """Write a fixture derived from the captured payload with NETs pushed into the future."""
    now = datetime.now(UTC).replace(microsecond=0)
    out = []
    for i, r in enumerate(json.loads(json.dumps(results))):
        net = now + timedelta(hours=6 * (i + 1))
        r["net"] = net.strftime("%Y-%m-%dT%H:%M:%SZ")
        if r["status"]["abbrev"] in {"Success", "Failure", "Partial Failure"}:
            r["status"] = {"id": 1, "abbrev": "Go", "name": "Go for Launch"}
        out.append(r)
    if hero_status:
        out[0]["status"] = {"id": 5, "abbrev": hero_status, "name": hero_status}
        out[0]["last_updated"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if hero_net_offset is not None:
        out[0]["net"] = (now + hero_net_offset).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps({"count": len(out), "results": out}))
    return path


@pytest.fixture
def handler(monkeypatch, tmp_path):
    with mock_aws():
        table = _create_table()
        payload = json.loads((FIXTURES / "ll2_upcoming_2.2.0.json").read_text())
        results = payload["results"]

        def load(path):
            monkeypatch.setenv("FIXTURE_FILE", str(path))
            import ingest_handler

            importlib.reload(ingest_handler)
            return ingest_handler

        yield {"table": table, "results": results, "load": load, "tmp": tmp_path}


def _items(table, prefix):
    resp = table.scan()
    return [i for i in resp["Items"] if i["pk"].startswith(prefix)]


def test_first_run_writes_all_and_reserves_one_call(handler):
    path = _write_fixture(handler["tmp"], handler["results"])
    mod = handler["load"](path)
    result = mod.lambda_handler({"job": "upcoming"}, None)

    assert result["ok"] and result["fetched"] == 20 and result["written"] == 20
    launches = _items(handler["table"], "LAUNCH#")
    assert len(launches) == 20
    assert all(i["lane"] == "UPCOMING" for i in launches)
    assert all("first_seen_at" in i and "last_seen_at" in i for i in launches)

    budget = _items(handler["table"], "RATE#LL2")
    assert len(budget) == 1 and budget[0]["calls"] == 1

    marker = handler["table"].get_item(Key={"pk": "META#INGEST", "sk": "JOB#upcoming"})["Item"]
    assert marker["ok"] is True and marker["items_seen"] == 20 and "last_ok_at" in marker


def test_second_identical_run_only_touches(handler):
    path = _write_fixture(handler["tmp"], handler["results"])
    mod = handler["load"](path)
    mod.lambda_handler({"job": "upcoming"}, None)
    result = mod.lambda_handler({"job": "upcoming"}, None)
    assert result["written"] == 0 and result["touched"] == 20
    budget = _items(handler["table"], "RATE#LL2")[0]
    assert budget["calls"] == 2


def test_status_change_is_detected_and_prev_status_kept(handler):
    mod = handler["load"](_write_fixture(handler["tmp"], handler["results"]))
    mod.lambda_handler({"job": "upcoming"}, None)
    hero_id = handler["results"][0]["id"]

    mod = handler["load"](_write_fixture(handler["tmp"], handler["results"], hero_status="Hold"))
    result = mod.lambda_handler({"job": "upcoming"}, None)
    assert result["status_changes"] == 1
    item = handler["table"].get_item(Key={"pk": f"LAUNCH#{hero_id}", "sk": "META"})["Item"]
    assert item["status_abbrev"] == "Hold"
    assert item["prev_status_abbrev"] == "Go"
    assert item["lane"] == "UPCOMING"


def test_cleared_upstream_fields_are_removed(handler):
    """Hold lifted and webcast pulled upstream -> holdreason / webcast_url must disappear."""
    hero_id = handler["results"][0]["id"]
    held = json.loads(json.dumps(handler["results"]))
    held[0]["holdreason"] = "Weather"
    held[0]["vidURLs"] = [{"priority": 10, "url": "https://youtube.com/live"}]
    mod = handler["load"](_write_fixture(handler["tmp"], held, hero_status="Hold"))
    mod.lambda_handler({"job": "upcoming"}, None)
    item = handler["table"].get_item(Key={"pk": f"LAUNCH#{hero_id}", "sk": "META"})["Item"]
    assert item["holdreason"] == "Weather" and item["webcast_url"] == "https://youtube.com/live"

    cleared = json.loads(json.dumps(handler["results"]))
    cleared[0]["holdreason"] = ""
    cleared[0]["vidURLs"] = []
    cleared[0]["last_updated"] = "2099-01-01T00:00:00Z"  # newer than the Hold write
    mod = handler["load"](_write_fixture(handler["tmp"], cleared))
    result = mod.lambda_handler({"job": "upcoming"}, None)
    assert result["written"] >= 1
    item = handler["table"].get_item(Key={"pk": f"LAUNCH#{hero_id}", "sk": "META"})["Item"]
    assert item["status_abbrev"] == "Go"
    assert "holdreason" not in item
    assert "webcast_url" not in item
    assert item["prev_status_abbrev"] == "Hold"


def test_stale_upstream_never_regresses_state(handler):
    """A later invocation carrying an older last_updated must not overwrite newer data."""
    fresh = _write_fixture(handler["tmp"], handler["results"], hero_status="Hold")
    mod = handler["load"](fresh)
    mod.lambda_handler({"job": "upcoming"}, None)

    stale_results = json.loads(json.dumps(handler["results"]))
    stale_results[0]["last_updated"] = "2020-01-01T00:00:00Z"
    stale = _write_fixture(handler["tmp"], stale_results)
    mod = handler["load"](stale)
    result = mod.lambda_handler({"job": "upcoming"}, None)
    assert result["skipped"] == 1
    hero_id = handler["results"][0]["id"]
    item = handler["table"].get_item(Key={"pk": f"LAUNCH#{hero_id}", "sk": "META"})["Item"]
    assert item["status_abbrev"] == "Hold"


def test_launch_dropped_from_feed_and_long_past_is_demoted(handler):
    # Seed a stale UPCOMING launch that will not appear in the fetch.
    old_net = (datetime.now(UTC) - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    handler["table"].put_item(
        Item={
            "pk": "LAUNCH#ghost",
            "sk": "META",
            "id": "ghost",
            "name": "Ghost",
            "net": old_net,
            "lane": "UPCOMING",
            "gsi1pk": "LANE#UPCOMING",
            "gsi1sk": f"{old_net}#ghost",
        }
    )
    # And a future one that is merely outside limit=20; it must be left alone.
    future_net = (datetime.now(UTC) + timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    handler["table"].put_item(
        Item={
            "pk": "LAUNCH#later",
            "sk": "META",
            "id": "later",
            "name": "Later",
            "net": future_net,
            "lane": "UPCOMING",
            "gsi1pk": "LANE#UPCOMING",
            "gsi1sk": f"{future_net}#later",
        }
    )
    mod = handler["load"](_write_fixture(handler["tmp"], handler["results"]))
    result = mod.lambda_handler({"job": "upcoming"}, None)
    assert result["demoted"] == 1
    ghost = handler["table"].get_item(Key={"pk": "LAUNCH#ghost", "sk": "META"})["Item"]
    later = handler["table"].get_item(Key={"pk": "LAUNCH#later", "sk": "META"})["Item"]
    assert ghost["lane"] == "PAST" and ghost["gsi1pk"] == "LANE#PAST"
    assert later["lane"] == "UPCOMING"


def test_budget_exhaustion_skips_fetch(handler, monkeypatch):
    monkeypatch.setenv("HOURLY_CALL_CAP", "2")
    mod = handler["load"](_write_fixture(handler["tmp"], handler["results"]))
    assert mod.lambda_handler({"job": "upcoming"}, None)["ok"]
    assert mod.lambda_handler({"job": "upcoming"}, None)["ok"]
    third = mod.lambda_handler({"job": "upcoming"}, None)
    assert third["ok"] is False and third["error"] == "budget_exhausted"
    budget = _items(handler["table"], "RATE#LL2")[0]
    assert budget["calls"] == 2  # the refused reservation consumed nothing
    marker = handler["table"].get_item(Key={"pk": "META#INGEST", "sk": "JOB#upcoming"})["Item"]
    assert marker["ok"] is False and "last_ok_at" in marker  # previous success preserved


def test_fetch_error_retries_once_then_gives_up(handler, monkeypatch):
    mod = handler["load"](_write_fixture(handler["tmp"], handler["results"]))
    calls = {"n": 0}

    def boom(url):
        calls["n"] += 1
        raise mod.ll2.FetchError("LL2 HTTP 503", status=503, retryable=True)

    monkeypatch.setattr(mod, "_load_payload", boom)
    result = mod.lambda_handler({"job": "upcoming"}, None)
    assert result["ok"] is False and result["status"] == 503
    assert calls["n"] == 2
    assert _items(handler["table"], "RATE#LL2")[0]["calls"] == 2


def test_429_is_never_retried(handler, monkeypatch):
    mod = handler["load"](_write_fixture(handler["tmp"], handler["results"]))
    calls = {"n": 0}

    def limited(url):
        calls["n"] += 1
        raise mod.ll2.RateLimited("60")

    monkeypatch.setattr(mod, "_load_payload", limited)
    result = mod.lambda_handler({"job": "upcoming"}, None)
    assert result["ok"] is False and result["error"] == "rate_limited"
    assert calls["n"] == 1


def test_unknown_job_rejected(handler):
    mod = handler["load"](_write_fixture(handler["tmp"], handler["results"]))
    with pytest.raises(ValueError):
        mod.lambda_handler({"job": "everything"}, None)

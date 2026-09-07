import importlib
import json
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

TABLE = "starbase-launches-test"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _launch(i, status="Go", net=None, lane="UPCOMING", **extra):
    now = datetime.now(UTC)
    net = net or now + timedelta(hours=i + 1)
    item = {
        "pk": f"LAUNCH#L{i}",
        "sk": "META",
        "id": f"L{i}",
        "name": f"Launch {i}",
        "net": _iso(net),
        "net_precision": "SEC",
        "status_abbrev": status,
        "status_name": status,
        "lane": lane,
        "gsi1pk": f"LANE#{lane}",
        "gsi1sk": f"{_iso(net)}#L{i}",
        "provider_name": "SpaceX",
        "vehicle_name": "Falcon 9",
        "pad_name": "SLC-40",
        "pad_location": "Cape",
        "webcast_live": False,
        "ll_last_updated": _iso(now),
        "last_seen_at": _iso(now),
    }
    item.update(extra)
    return item


def _event(path, qs=None, path_params=None, method="GET"):
    return {
        "rawPath": path,
        "queryStringParameters": qs or {},
        "pathParameters": path_params or {},
        "requestContext": {"http": {"method": method}},
    }


@pytest.fixture
def env():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
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
        import read_handler

        importlib.reload(read_handler)
        yield table, read_handler


def _body(resp):
    return json.loads(resp["body"])


def test_board_hero_and_strip_sorted_by_net(env):
    table, mod = env
    for i in range(12):
        table.put_item(Item=_launch(i))
    table.put_item(
        Item={
            "pk": "META#INGEST",
            "sk": "JOB#upcoming",
            "job": "upcoming",
            "last_ok_at": _iso(datetime.now(UTC)),
            "last_run_at": _iso(datetime.now(UTC)),
            "ok": True,
        }
    )
    resp = mod.lambda_handler(_event("/api/v1/board", {"limit": "8"}))
    assert resp["statusCode"] == 200
    assert resp["headers"]["Cache-Control"] == "public, max-age=30"
    body = _body(resp)
    assert body["hero"]["id"] == "L0"
    assert [s["id"] for s in body["strip"]] == [f"L{i}" for i in range(1, 9)]
    assert body["source"]["by"] == "The Space Devs"
    assert body["ingest"]["stale"] is False


def test_board_prefers_in_flight_hero(env):
    table, mod = env
    table.put_item(Item=_launch(0))
    table.put_item(Item=_launch(1, status="In Flight"))
    body = _body(mod.lambda_handler(_event("/api/v1/board")))
    assert body["hero"]["id"] == "L1"
    assert [s["id"] for s in body["strip"]] == ["L0"]


def test_board_excludes_long_past_and_past_lane(env):
    table, mod = env
    table.put_item(Item=_launch(0, net=datetime.now(UTC) - timedelta(hours=10)))  # stale, not yet demoted
    table.put_item(Item=_launch(1, status="Success", lane="PAST"))
    table.put_item(Item=_launch(2))
    body = _body(mod.lambda_handler(_event("/api/v1/board")))
    assert body["hero"]["id"] == "L2"
    assert body["strip"] == []


def test_board_within_grace_still_shows(env):
    table, mod = env
    table.put_item(Item=_launch(0, net=datetime.now(UTC) - timedelta(hours=1)))
    body = _body(mod.lambda_handler(_event("/api/v1/board")))
    assert body["hero"]["id"] == "L0"


def test_empty_board(env):
    _, mod = env
    body = _body(mod.lambda_handler(_event("/api/v1/board")))
    assert body["hero"] is None and body["strip"] == []
    assert body["ingest"]["stale"] is True


def test_launches_past_desc_and_bad_lane(env):
    table, mod = env
    now = datetime.now(UTC)
    for i in range(3):
        table.put_item(Item=_launch(i, status="Success", lane="PAST", net=now - timedelta(days=i + 1)))
    body = _body(mod.lambda_handler(_event("/api/v1/launches", {"lane": "past", "limit": "2"})))
    assert [x["id"] for x in body["launches"]] == ["L0", "L1"]
    assert mod.lambda_handler(_event("/api/v1/launches", {"lane": "sideways"}))["statusCode"] == 400


def test_launch_by_id_and_404_and_bad_id(env):
    table, mod = env
    table.put_item(Item=_launch(0, webcast_url="https://youtube.com/x"))
    ok = mod.lambda_handler(_event("/api/v1/launches/L0", path_params={"id": "L0"}))
    assert ok["statusCode"] == 200 and _body(ok)["launch"]["webcast_url"] == "https://youtube.com/x"
    assert mod.lambda_handler(_event("/api/v1/launches/nope", path_params={"id": "nope"}))["statusCode"] == 404
    assert mod.lambda_handler(_event("/api/v1/launches/x y", path_params={"id": "x y"}))["statusCode"] == 400


def test_health_503_when_stale(env):
    table, mod = env
    old = _iso(datetime.now(UTC) - timedelta(hours=2))
    table.put_item(Item={"pk": "META#INGEST", "sk": "JOB#upcoming", "job": "upcoming", "last_ok_at": old, "ok": True})
    resp = mod.lambda_handler(_event("/api/v1/health"))
    assert resp["statusCode"] == 503
    assert _body(resp)["status"] == "degraded"
    table.put_item(
        Item={
            "pk": "META#INGEST",
            "sk": "JOB#upcoming",
            "job": "upcoming",
            "last_ok_at": _iso(datetime.now(UTC)),
            "ok": True,
        }
    )
    assert mod.lambda_handler(_event("/api/v1/health"))["statusCode"] == 200


def test_unknown_route_and_method(env):
    _, mod = env
    assert mod.lambda_handler(_event("/api/v1/nothing"))["statusCode"] == 404
    assert mod.lambda_handler(_event("/api/v1/board", method="POST"))["statusCode"] == 405

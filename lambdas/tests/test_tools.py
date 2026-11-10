"""CAPCOM's tools see exactly what the read API sees (shared Reader), and
nothing beyond the table — the Knowledge Boundary (ADR 0001)."""

import importlib
from datetime import UTC, datetime, timedelta

import boto3
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
        "holdreason": None,
    }
    item.update({k: v for k, v in extra.items() if v is not None})
    return item


def test_tools_match_the_read_api_shaping():
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
        table.put_item(Item=_launch(0, holdreason="range"))
        table.put_item(Item=_launch(1))
        table.put_item(Item=_launch(2, status="Success", lane="PAST", net=datetime.now(UTC) - timedelta(days=1)))
        table.put_item(
            Item={
                "pk": "META#INGEST",
                "sk": "JOB#upcoming",
                "job": "upcoming",
                "last_ok_at": _iso(datetime.now(UTC)),
                "ok": True,
            }
        )

        import tools

        importlib.reload(tools)

        board = tools.get_board(8)
        assert board["hero"]["id"] == "L0"
        assert [s["id"] for s in board["strip"]] == ["L1"]
        assert board["hero"]["holdreason"] == "range"  # same public shape as the read API
        assert board["ingest"]["stale"] is False

        past = tools.list_launches("past")
        assert [x["id"] for x in past["launches"]] == ["L2"]
        assert past["lane"] == "past"

        upcoming = tools.list_launches("sideways")  # unknown lane falls back, never errors
        assert upcoming["lane"] == "upcoming" and upcoming["count"] == 2

        one = tools.get_launch("L0")
        assert one["launch"]["vehicle"] == "Falcon 9"
        assert tools.get_launch("nope")["error"] == "not_found"

        assert tools.get_health()["status"] == "ok"


def test_limits_clamp_to_bounded_reads():
    import tools

    importlib.reload(tools)

    assert tools._clamp_limit(999) == 20  # never a table scan
    assert tools._clamp_limit(0) == 1
    assert tools._clamp_limit(-5) == 1
    assert tools._clamp_limit("x") == 20  # junk falls back to the cap, not an error
    assert tools._clamp_limit("7") == 7

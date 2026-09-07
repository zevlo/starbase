import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import FIXTURES

import mapping

NOW = datetime(2026, 9, 7, 5, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def payload():
    return json.loads((FIXTURES / "ll2_upcoming_2.2.0.json").read_text())


def test_fixture_maps_without_errors(payload):
    items = [mapping.to_item(r, NOW) for r in payload["results"]]
    assert len(items) == 20
    for it in items:
        assert it["pk"].startswith("LAUNCH#")
        assert it["sk"] == "META"
        assert it["gsi1pk"] in {"LANE#UPCOMING", "LANE#PAST"}
        assert it["gsi1sk"] == f"{it['net']}#{it['id']}"
        assert it["status_abbrev"] in mapping.KNOWN_STATUSES
        assert isinstance(it["webcast_live"], bool)
        assert isinstance(it["expires_at"], int)
        assert None not in it.values()


def test_success_launch_goes_to_past_lane(payload):
    first = payload["results"][0]
    assert first["status"]["abbrev"] == "Success"
    item = mapping.to_item(first, NOW)
    assert item["lane"] == "PAST"
    assert item["gsi1pk"] == "LANE#PAST"


def test_gsi_sort_key_orders_chronologically(payload):
    items = [mapping.to_item(r, NOW) for r in payload["results"]]
    nets = [mapping.parse_iso(i["net"]) for i in items]
    keys = [i["gsi1sk"] for i in items]
    assert [k for _, k in sorted(zip(nets, keys, strict=True))] == sorted(keys)


def test_webcast_url_prefers_highest_priority():
    raw = {
        "vidURLs": [
            {"priority": 8, "url": "https://a"},
            {"priority": 12, "url": "https://b"},
            {"priority": 3, "url": "https://c"},
            {"url": "https://no-priority"},
        ]
    }
    assert mapping.pick_webcast_url(raw) == "https://b"
    assert mapping.pick_webcast_url({"vidURLs": []}) is None
    assert mapping.pick_webcast_url({}) is None
    assert mapping.pick_webcast_url({"vid_urls": [{"priority": 1, "url": "https://v3"}]}) == "https://v3"


def test_image_url_string_or_object():
    assert mapping.pick_image_url({"image": "https://img"}) == "https://img"
    assert mapping.pick_image_url({"image": {"image_url": "https://obj"}}) == "https://obj"
    assert mapping.pick_image_url({"image": None}) is None
    assert mapping.pick_image_url({"image": ""}) is None
    assert mapping.pick_image_url({}) is None


@pytest.mark.parametrize(
    "status,net_offset,expected",
    [
        ("Go", timedelta(hours=1), "UPCOMING"),
        ("Go", -timedelta(hours=1), "UPCOMING"),  # within 6h grace
        ("Go", -timedelta(hours=7), "PAST"),
        ("TBD", timedelta(days=30), "UPCOMING"),
        ("Hold", timedelta(minutes=5), "UPCOMING"),
        ("In Flight", -timedelta(hours=10), "UPCOMING"),  # In Flight always upcoming
        ("Success", timedelta(hours=1), "PAST"),
        ("Failure", timedelta(hours=1), "PAST"),
        ("Partial Failure", -timedelta(hours=1), "PAST"),
    ],
)
def test_compute_lane(status, net_offset, expected):
    assert mapping.compute_lane(status, NOW + net_offset, NOW) == expected


def test_unknown_status_normalizes_to_tbd():
    raw = {"id": "x", "net": "2026-09-10T00:00:00Z", "status": {"abbrev": "Weird"}}
    item = mapping.to_item(raw, NOW)
    assert item["status_abbrev"] == "TBD"
    assert item["status_raw_abbrev"] == "Weird"


def test_ttl_is_net_plus_30_days():
    raw = {"id": "x", "net": "2026-09-10T00:00:00Z", "status": {"abbrev": "Go"}}
    item = mapping.to_item(raw, NOW)
    assert item["expires_at"] == int((mapping.parse_iso("2026-09-10T00:00:00Z") + timedelta(days=30)).timestamp())


def test_description_truncated():
    raw = {"id": "x", "net": "2026-09-10T00:00:00Z", "status": {"abbrev": "Go"}, "mission": {"description": "a" * 900}}
    item = mapping.to_item(raw, NOW)
    assert len(item["mission_description"]) <= mapping.DESCRIPTION_MAX


def test_missing_id_or_net_raises():
    with pytest.raises(mapping.MappingError):
        mapping.to_item({"net": "2026-09-10T00:00:00Z"}, NOW)
    with pytest.raises(mapping.MappingError):
        mapping.to_item({"id": "x"}, NOW)
    with pytest.raises(mapping.MappingError):
        mapping.to_item({"id": "x", "net": "not a date"}, NOW)


def test_hold_fixture_preserves_holdreason(payload):
    raw = dict(payload["results"][1])
    raw["status"] = {"id": 5, "abbrev": "Hold", "name": "On Hold"}
    raw["holdreason"] = "Weather"
    item = mapping.to_item(raw, NOW)
    assert item["status_abbrev"] == "Hold"
    assert item["holdreason"] == "Weather"
    assert item["lane"] == "UPCOMING"

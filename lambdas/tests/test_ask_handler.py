import importlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from moto import mock_aws

TABLE = "starbase-launches-test"
SESSION = str(uuid.uuid4())


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(question="next launch?", session_id=SESSION, method="POST"):
    return {
        "requestContext": {"http": {"method": method}},
        "body": json.dumps({"question": question, "session_id": session_id}),
    }


def _body(resp):
    return json.loads(resp["body"])


def _throttle():
    return ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "InvokeAgentRuntime")


class FakeRuntime:
    """Queued outcomes per invoke: exceptions raise, strings are the answer."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.last_kwargs = None

    def invoke_agent_runtime(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return {"response": json.dumps({"answer": outcome, "ok": True}).encode(), "statusCode": 200}


@pytest.fixture
def ask(monkeypatch):
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
        )
        import ask_handler

        importlib.reload(ask_handler)
        yield table, ask_handler, monkeypatch


def _wire_runtime(monkeypatch, module, outcomes):
    fake = FakeRuntime(outcomes)
    monkeypatch.setattr(module.boto3, "client", lambda *a, **k: fake)
    return fake


def test_ok_invokes_runtime_with_session_and_no_store_header(ask):
    table, mod, monkeypatch = ask
    fake = _wire_runtime(monkeypatch, mod, ["Go for launch."])
    resp = mod.lambda_handler(_event())
    assert resp["statusCode"] == 200
    assert resp["headers"]["Cache-Control"] == "no-store"
    body = _body(resp)
    assert body["answer"] == "Go for launch."
    assert body["session_id"] == SESSION
    assert fake.last_kwargs["agentRuntimeArn"] == mod.AGENT_RUNTIME_ARN
    assert fake.last_kwargs["runtimeSessionId"] == SESSION
    assert json.loads(fake.last_kwargs["payload"])["question"] == "next launch?"
    assert (
        table.get_item(Key={"pk": "ASK#CAPCOM", "sk": f"DAY#{datetime.now(UTC).strftime('%Y-%m-%d')}"})["Item"]["calls"]
        == 1
    )


def test_validation_rejects_before_spending_quota(ask):
    table, mod, _ = ask
    bad = [
        _event(question="   "),
        _event(question="x" * 501),
        _event(session_id="not-a-uuid"),
        {"requestContext": {"http": {"method": "POST"}}, "body": "{nope"},
    ]
    for event in bad:
        resp = mod.lambda_handler(event)
        assert resp["statusCode"] == 400
    assert mod.lambda_handler(_event(method="GET"))["statusCode"] == 405
    # nothing consumed: no quota item ever written
    days = table.query(KeyConditionExpression=boto3.dynamodb.conditions.Key("pk").eq("ASK#CAPCOM"))
    assert days["Count"] == 0


def test_daily_quota_caps_and_refusal_consumes_nothing(ask):
    table, mod, monkeypatch = ask
    monkeypatch.setenv("DAILY_LIMIT", "2")
    importlib.reload(mod)
    fake = _wire_runtime(monkeypatch, mod, ["a", "b"])
    assert mod.lambda_handler(_event())["statusCode"] == 200
    assert mod.lambda_handler(_event())["statusCode"] == 200
    capped = mod.lambda_handler(_event())
    assert capped["statusCode"] == 429
    assert _body(capped)["error"] == "daily_quota"
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    assert table.get_item(Key={"pk": "ASK#CAPCOM", "sk": f"DAY#{day}"})["Item"]["calls"] == 2
    assert fake.calls == 2  # the refused question never reached the runtime


def test_quota_resets_at_utc_midnight(ask):
    table, mod, _ = ask
    now = datetime.now(UTC)
    assert mod.reserve_question(table, now, 1) is True
    assert mod.reserve_question(table, now, 1) is False
    assert mod.reserve_question(table, now + timedelta(days=1), 1) is True  # new key, fresh budget


def test_throttle_retried_once_then_503(ask):
    _, mod, monkeypatch = ask
    fake = _wire_runtime(monkeypatch, mod, [_throttle(), "recovered", _throttle(), _throttle()])
    assert mod.lambda_handler(_event())["statusCode"] == 200
    assert (
        _body(
            mod.lambda_handler(
                _event(  # second question throttles even after the retry
                    question="again?", session_id=str(uuid.uuid4())
                )
            )
        )["error"]
        == "capcom_busy"
    )
    assert fake.calls == 4  # one retry per turn, then give up


def test_runtime_hard_errors(ask):
    _, mod, monkeypatch = ask
    _wire_runtime(monkeypatch, mod, [ReadTimeoutError(endpoint_url="x")])
    assert _body(mod.lambda_handler(_event()))["error"] == "capcom_timeout"

    _wire_runtime(monkeypatch, mod, [ClientError({"Error": {"Code": "AccessDeniedException"}}, "InvokeAgentRuntime")])
    resp = mod.lambda_handler(_event(question="one more?", session_id=str(uuid.uuid4())))
    assert resp["statusCode"] == 502
    assert _body(resp)["error"] == "capcom_unreachable"

import hashlib
import hmac
import importlib.util
import json
import time
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

SIGNING_SECRET = "test-signing-secret"
INGEST_PATH = (
    Path(__file__).resolve().parents[2]
    / "infrastructure"
    / "lambdaFunctions"
    / "slackIngest"
    / "app.py"
)


def _load_ingest():
    # mcpServer/app.py already owns the module name "app"; load ingest by path
    spec = importlib.util.spec_from_file_location("ingest_app", INGEST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _signed_event(body: dict | str, secret: str = SIGNING_SECRET, ts: int | None = None) -> dict:
    raw = body if isinstance(body, str) else json.dumps(body)
    ts = ts if ts is not None else int(time.time())
    base = f"v0:{ts}:{raw}".encode()
    sig = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return {
        "body": raw,
        "headers": {
            "x-slack-request-timestamp": str(ts),
            "x-slack-signature": sig,
        },
    }


def _message_callback(channel="C123", ts="1700000000.000100", text="hello", **event_extra):
    return {
        "type": "event_callback",
        "event_id": "Ev001",
        "event": {
            "type": "message",
            "channel": channel,
            "ts": ts,
            "text": text,
            "user": "U777",
            **event_extra,
        },
    }


@pytest.fixture()
def ingest(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SIGNING_SECRET)
    monkeypatch.setenv("RELAY_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("MESSAGES_TABLE", "test-messages")
    with mock_aws():
        boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="test-messages",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        module = _load_ingest()
        # Slack API is out of reach in unit tests; default to "unknown user"
        module._lookup_user = lambda user_id: ""
        yield module


def _items(table_name="test-messages"):
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(table_name)
    return [i for i in table.scan()["Items"] if i["PK"].startswith("CH#")]


def test_url_verification_echoes_challenge(ingest):
    resp = ingest.handler(
        _signed_event({"type": "url_verification", "challenge": "abc123"}), None
    )
    assert resp == {"statusCode": 200, "body": "abc123"}


def test_rejects_bad_signature(ingest):
    event = _signed_event(_message_callback(), secret="wrong-secret")
    assert ingest.handler(event, None)["statusCode"] == 401
    assert _items() == []


def test_rejects_stale_timestamp(ingest):
    event = _signed_event(_message_callback(), ts=int(time.time()) - 3600)
    assert ingest.handler(event, None)["statusCode"] == 401


def test_stores_human_message_with_mentions(ingest):
    body = _message_callback(text="hey <@U0AGENT1> and <@U0AGENT2>")
    assert ingest.handler(_signed_event(body), None)["statusCode"] == 200

    items = _items()
    assert len(items) == 1
    item = items[0]
    assert item["PK"] == "CH#C123"
    assert item["SK"] == "TS#1700000000.000100"
    assert item["sender_type"] == "human"
    assert item["mentions"] == ["U0AGENT1", "U0AGENT2"]
    assert item["slack_event_id"] == "Ev001"


def test_agent_metadata_sets_sender(ingest):
    body = _message_callback(
        bot_id="B9",
        metadata={"event_type": "agent_message", "event_payload": {"agent_id": "wilma"}},
    )
    del body["event"]["user"]
    ingest.handler(_signed_event(body), None)

    item = _items()[0]
    assert item["sender_type"] == "agent"
    assert item["agent_id"] == "wilma"
    assert item["user"] == "B9"


def test_duplicate_delivery_is_idempotent(ingest):
    body = _message_callback()
    ingest.handler(_signed_event(body), None)
    resp = ingest.handler(_signed_event(body), None)
    assert resp["statusCode"] == 200
    assert len(_items()) == 1


def test_user_names_resolved_and_cached(ingest, monkeypatch):
    calls = []

    def fake_lookup(user_id):
        calls.append(user_id)
        return {"U777": "Justin Bard", "U0AGENT1": "WILMA"}.get(user_id, "")

    monkeypatch.setattr(ingest, "_lookup_user", fake_lookup)
    ingest._USER_NAMES.clear()

    ingest.handler(
        _signed_event(_message_callback(text="hey <@U0AGENT1>", ts="1.0")), None
    )
    ingest.handler(
        _signed_event(_message_callback(text="again <@U0AGENT1>", ts="2.0")), None
    )

    items = {i["SK"]: i for i in _items() if i["PK"] == "CH#C123"}
    assert items["TS#1.0"]["user_name"] == "Justin Bard"
    assert items["TS#1.0"]["mention_names"] == ["WILMA"]
    assert items["TS#2.0"]["user_name"] == "Justin Bard"
    # One lookup per distinct user, not per message
    assert sorted(calls) == ["U0AGENT1", "U777"]


def test_lookup_failure_never_blocks_ingest(ingest, monkeypatch):
    def boom(user_id):
        raise RuntimeError("slack down")

    monkeypatch.setattr(ingest, "_lookup_user", boom)
    ingest._USER_NAMES.clear()

    resp = ingest.handler(_signed_event(_message_callback()), None)

    assert resp["statusCode"] == 200
    assert _items()[0]["user_name"] == ""


def test_publishes_after_store_but_not_on_duplicate(ingest, monkeypatch):
    published = []
    monkeypatch.setattr(ingest, "_publish", published.append)

    body = _message_callback(text="push me")
    ingest.handler(_signed_event(body), None)
    ingest.handler(_signed_event(body), None)  # duplicate delivery

    assert len(published) == 1
    payload = published[0]
    assert payload["text"] == "push me"
    assert "PK" not in payload and "SK" not in payload and "ttl" not in payload


def test_publish_failure_does_not_fail_ingest(ingest, monkeypatch):
    def boom(_):
        raise RuntimeError("events api down")

    monkeypatch.setattr(ingest, "_publish", boom)
    resp = ingest.handler(_signed_event(_message_callback()), None)

    assert resp["statusCode"] == 200
    assert len(_items()) == 1


def test_ignores_edits_and_noise(ingest):
    for subtype in ("message_changed", "message_deleted", "channel_join"):
        ingest.handler(_signed_event(_message_callback(subtype=subtype)), None)
    assert _items() == []

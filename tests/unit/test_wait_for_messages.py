import boto3
import pytest
import tools.wait_for_messages as wm
from moto import mock_aws
from sharedModules import dynamo


class FakeSubscription:
    """Stands in for the AppSync WebSocket: yields queued events, then None."""

    instances = []

    def __init__(self, channel):
        self.channel = channel
        self.events = []
        self.connected = False
        self.closed = False
        FakeSubscription.instances.append(self)

    def connect(self, timeout=10.0):
        self.connected = True

    def next_event(self, deadline):
        return self.events.pop(0) if self.events else None

    def close(self):
        self.closed = True


@pytest.fixture()
def table(monkeypatch):
    monkeypatch.setenv("MESSAGES_TABLE", "test-messages")
    monkeypatch.setattr(wm, "EventSubscription", FakeSubscription)
    FakeSubscription.instances = []
    dynamo.messages_table.cache_clear()
    with mock_aws():
        t = boto3.resource("dynamodb", region_name="us-east-1").create_table(
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
        yield t
    dynamo.messages_table.cache_clear()


def _event(ts, text, agent_id="", channel="C123"):
    return {
        "channel": channel,
        "ts": ts,
        "thread_ts": "",
        "text": text,
        "user": "U1",
        "sender_type": "agent" if agent_id else "human",
        "agent_id": agent_id,
        "mentions": [],
    }


def test_backlog_returns_immediately(table):
    table.put_item(Item={**_event("1.0", "pending"), "PK": "CH#C123", "SK": "TS#1.0"})

    result = wm.wait_for_messages(timeout_seconds=30, channel="C123")

    assert result["ok"] is True
    assert [m["text"] for m in result["messages"]] == ["pending"]
    sub = FakeSubscription.instances[0]
    assert sub.connected and sub.closed
    assert sub.channel == "slack/messages/C123"


def test_live_event_returned_and_cursor_advanced(table):
    result_channel = "slack/messages/*"

    def run():
        return wm.wait_for_messages(timeout_seconds=30, channel="C123")

    # Pre-create the subscription queue via the class hook: patch connect to load events
    orig_connect = FakeSubscription.connect

    def connect_with_events(self, timeout=10.0):
        orig_connect(self, timeout)
        self.events = [_event("2.0", "live reply")]

    FakeSubscription.connect = connect_with_events
    try:
        result = run()
    finally:
        FakeSubscription.connect = orig_connect

    assert [m["text"] for m in result["messages"]] == ["live reply"]
    assert dynamo.get_cursor("wilma", "C123") == "2.0"
    assert result_channel  # silence unused warning pattern


def test_own_echo_skipped_then_timeout(table):
    orig_connect = FakeSubscription.connect

    def connect_with_events(self, timeout=10.0):
        orig_connect(self, timeout)
        self.events = [_event("3.0", "my own echo", agent_id="wilma")]

    FakeSubscription.connect = connect_with_events
    try:
        result = wm.wait_for_messages(timeout_seconds=5, channel="C123")
    finally:
        FakeSubscription.connect = orig_connect

    assert result["messages"] == []
    assert result["timed_out"] is True
    # The echo still advanced the cursor so check_messages will not replay it
    assert dynamo.get_cursor("wilma", "C123") == "3.0"


def test_timeout_and_session_heartbeat(table):
    result = wm.wait_for_messages(timeout_seconds=5, channel="C123")

    assert result == {"ok": True, "messages": [], "timed_out": True}
    session = table.get_item(Key={"PK": "SESSION#wilma", "SK": "META"}).get("Item")
    assert session is not None
    assert int(session["ttl"]) > 0

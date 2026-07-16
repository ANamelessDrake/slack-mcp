import boto3
import pytest
import tools.check_messages as cm
import tools.wait_for_messages as wm
from moto import mock_aws
from sharedModules import dynamo


class FakeSubscription:
    def __init__(self, channel):
        self.channel = channel
        self.events = []
        self.closed = False

    def connect(self, timeout=10.0):
        pass

    def next_event(self, deadline):
        return self.events.pop(0) if self.events else None

    def close(self):
        self.closed = True


@pytest.fixture()
def table(monkeypatch):
    monkeypatch.setenv("MESSAGES_TABLE", "test-messages")
    monkeypatch.setattr(wm, "EventSubscription", FakeSubscription)
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


def _seed(table, channel, ts, text, user="U1"):
    table.put_item(
        Item={
            "PK": f"CH#{channel}",
            "SK": f"TS#{ts}",
            "channel": channel,
            "ts": ts,
            "thread_ts": "",
            "text": text,
            "user": user,
            "sender_type": "human",
            "agent_id": "",
        }
    )


def _event(channel, ts, text, user="U1"):
    return {
        "channel": channel,
        "ts": ts,
        "thread_ts": "",
        "text": text,
        "user": user,
        "sender_type": "human",
        "agent_id": "",
        "mentions_agents": [],
    }


def test_check_accepts_comma_separated_channels(table):
    _seed(table, "C1", "1.0", "one")
    _seed(table, "C2", "1.0", "two")
    _seed(table, "C3", "1.0", "three, not requested")

    result = cm.check_messages("C1, C2")
    assert sorted(m["text"] for m in result["messages"]) == ["one", "two"]
    # C3 was untouched and still pending
    assert [m["text"] for m in cm.check_messages("C3")["messages"]] == [
        "three, not requested"
    ]


def test_check_from_user_filter(table):
    _seed(table, "C1", "1.0", "from justin", user="U0JUSTIN")
    _seed(table, "C1", "2.0", "from someone else", user="U0OTHER")

    result = cm.check_messages("C1", from_user="U0JUSTIN")
    assert [m["text"] for m in result["messages"]] == ["from justin"]


def test_wait_multi_channel_ignores_unwatched_without_consuming(table, monkeypatch):
    def connect_with_events(self, timeout=10.0):
        self.events = [
            _event("C9", "5.0", "unwatched traffic"),
            _event("C2", "6.0", "watched traffic"),
        ]

    monkeypatch.setattr(FakeSubscription, "connect", connect_with_events)

    result = wm.wait_for_messages(timeout_seconds=5, channel="C1,C2")

    assert [m["text"] for m in result["messages"]] == ["watched traffic"]
    # The unwatched channel's cursor never moved: its message stays new
    assert dynamo.get_cursor("wilma", "C9") == "0"
    assert dynamo.get_cursor("wilma", "C2") == "6.0"


def test_wait_from_user_skips_others(table, monkeypatch):
    def connect_with_events(self, timeout=10.0):
        self.events = [
            _event("C1", "1.0", "wrong person", user="U0OTHER"),
            _event("C1", "2.0", "right person", user="U0JUSTIN"),
        ]

    monkeypatch.setattr(FakeSubscription, "connect", connect_with_events)

    result = wm.wait_for_messages(timeout_seconds=5, channel="C1", from_user="U0JUSTIN")

    assert [m["text"] for m in result["messages"]] == ["right person"]


def test_wait_cap_raised_to_840():
    assert wm.MAX_WAIT_SECONDS == 840

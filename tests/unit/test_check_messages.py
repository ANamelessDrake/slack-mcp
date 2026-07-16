import boto3
import pytest
import tools.check_messages as cm
from moto import mock_aws
from sharedModules import dynamo


def _put_message(table, channel, ts, text="hi", agent_id="", sender_type="human"):
    table.put_item(
        Item={
            "PK": f"CH#{channel}",
            "SK": f"TS#{ts}",
            "channel": channel,
            "ts": ts,
            "thread_ts": "",
            "text": text,
            "user": "U1",
            "sender_type": sender_type,
            "agent_id": agent_id,
            "mentions": [],
        }
    )


@pytest.fixture()
def table(monkeypatch):
    monkeypatch.setenv("MESSAGES_TABLE", "test-messages")
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


def test_returns_new_messages_and_advances_cursor(table):
    _put_message(table, "C123", "1.000100", text="first")
    _put_message(table, "C123", "2.000100", text="second")

    result = cm.check_messages("C123")
    assert result["ok"] is True
    assert [m["text"] for m in result["messages"]] == ["first", "second"]

    # Second call: nothing new
    assert cm.check_messages("C123")["messages"] == []

    # New arrival after the cursor is picked up
    _put_message(table, "C123", "3.000100", text="third")
    assert [m["text"] for m in cm.check_messages("C123")["messages"]] == ["third"]


def test_filters_own_agent_messages(table):
    # conftest sets DEFAULT_AGENT_ID=claude; own echo must be skipped but consumed
    _put_message(table, "C123", "1.0", text="mine", agent_id="claude", sender_type="agent")
    _put_message(table, "C123", "2.0", text="from wilma", agent_id="wilma", sender_type="agent")

    result = cm.check_messages("C123")
    assert [m["text"] for m in result["messages"]] == ["from wilma"]

    # The echo was consumed by the cursor, not left pending
    assert cm.check_messages("C123")["messages"] == []


def test_empty_channel_checks_all_member_channels(table, monkeypatch):
    class FakeRelay:
        def conversations_list(self, **kwargs):
            return {
                "channels": [
                    {"id": "C1", "is_member": True},
                    {"id": "C2", "is_member": False},
                    {"id": "C3", "is_member": True},
                ]
            }

    monkeypatch.setattr(cm, "relay_client", lambda: FakeRelay())
    _put_message(table, "C1", "1.0", text="in C1")
    _put_message(table, "C2", "1.0", text="in C2, not a member")
    _put_message(table, "C3", "1.0", text="in C3")

    result = cm.check_messages()
    assert sorted(m["text"] for m in result["messages"]) == ["in C1", "in C3"]

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
    # conftest sets DEFAULT_AGENT_ID=wilma; own echo must be skipped but consumed
    _put_message(table, "C123", "1.0", text="mine", agent_id="wilma", sender_type="agent")
    _put_message(table, "C123", "2.0", text="from claude", agent_id="claude", sender_type="agent")

    result = cm.check_messages("C123")
    assert [m["text"] for m in result["messages"]] == ["from claude"]

    # The echo was consumed by the cursor, not left pending
    assert cm.check_messages("C123")["messages"] == []


def test_empty_channel_sweeps_registry_including_dms(table):
    # The ingest-maintained registry covers channels and DM conversations alike
    for ch in ("C1", "D0DM1"):
        table.put_item(Item={"PK": "CHANNELS", "SK": f"CH#{ch}", "channel": ch})
    _put_message(table, "C1", "1.0", text="in channel")
    _put_message(table, "D0DM1", "1.0", text="in DM")
    _put_message(table, "C9", "1.0", text="not registered, not swept")

    result = cm.check_messages()
    assert sorted(m["text"] for m in result["messages"]) == ["in DM", "in channel"]

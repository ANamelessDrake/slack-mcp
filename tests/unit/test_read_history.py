import boto3
import pytest
import tools.check_messages as cm
import tools.read_history as rh
from moto import mock_aws
from sharedModules import dynamo


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


def _seed(table, channel, ts, text, agent_id=""):
    table.put_item(
        Item={
            "PK": f"CH#{channel}",
            "SK": f"TS#{ts}",
            "channel": channel,
            "ts": ts,
            "text": text,
            "user": "U1",
            "user_name": "Justin Bard",
            "sender_type": "agent" if agent_id else "human",
            "agent_id": agent_id,
        }
    )


def test_returns_recent_oldest_first_including_own(table):
    _seed(table, "D0DM1", "1.0", "question", agent_id="")
    _seed(table, "D0DM1", "2.0", "agent answer", agent_id="wilma")
    _seed(table, "D0DM1", "3.0", "follow-up")

    result = rh.read_history("D0DM1")

    assert result["ok"] is True
    assert [m["text"] for m in result["messages"]] == [
        "question",
        "agent answer",
        "follow-up",
    ]


def test_limit_keeps_newest(table):
    for i in range(1, 6):
        _seed(table, "C123", f"{i}.0", f"msg{i}")

    result = rh.read_history("C123", limit=2)

    assert [m["text"] for m in result["messages"]] == ["msg4", "msg5"]


def test_history_read_does_not_consume(table):
    table.put_item(Item={"PK": "CHANNELS", "SK": "CH#C123", "channel": "C123"})
    _seed(table, "C123", "1.0", "unread")

    rh.read_history("C123")
    result = cm.check_messages("C123")

    assert [m["text"] for m in result["messages"]] == ["unread"]

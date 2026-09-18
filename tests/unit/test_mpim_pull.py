"""On-demand group-DM (mpim) pull.

Slack will not push message.mpim to bots, so group DMs are read by pulling
conversations.history on demand and writing into the same store the event ingest
uses. These tests pin: the pull writes and attributes messages, is idempotent
and incremental, and check_messages triggers it only for a named group DM, never
for an event-driven channel.
"""

import boto3
import pytest
import tools.check_messages as cm
from moto import mock_aws
from sharedModules import dynamo, mpim


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


def _put_agent(table, agent_id, bot_user_id):
    table.put_item(
        Item={
            "PK": "AGENTS",
            "SK": f"AGENT#{agent_id}",
            "agent_id": agent_id,
            "bot_user_id": bot_user_id,
        }
    )


class FakeAgentClient:
    """The agent's Slack app: returns a group DM's history."""

    def __init__(self, history=None):
        self._history = history or []  # newest-first, as Slack returns
        self.history_calls = []

    def conversations_history(self, **kwargs):
        self.history_calls.append(kwargs)
        return {"messages": self._history, "response_metadata": {"next_cursor": ""}}


def test_pull_writes_and_attributes_group_messages(table, monkeypatch):
    _put_agent(table, "wilma", "UWILMABOT")
    history = [  # newest first
        {"type": "message", "ts": "3.0", "user": "U1", "text": "hello from emily"},
        {"type": "message", "ts": "2.0", "user": "UWILMABOT", "text": "wilma reply"},
    ]
    fake = FakeAgentClient(history=history)
    monkeypatch.setattr(mpim, "agent_client", lambda _: fake)
    monkeypatch.setattr(mpim, "_resolve_name", lambda uid: {"U1": "Emily"}.get(uid, ""))

    written = mpim.pull_mpim_history("wilma", "C0GROUP")

    assert written == 2
    stored = {m["ts"]: m for m in dynamo.recent_messages("C0GROUP", 10)}
    # Oldest-first write order so the stored maximum ts advances cleanly
    assert list(stored) == ["2.0", "3.0"]
    # The agent's own post is attributed, so a later read skips it as an echo
    assert stored["2.0"]["sender_type"] == "agent"
    assert stored["2.0"]["agent_id"] == "wilma"
    assert stored["3.0"]["sender_type"] == "human"
    assert stored["3.0"]["user_name"] == "Emily"
    # Registered as mpim, and the first pull asks from the beginning
    assert dynamo.channel_type("C0GROUP") == "mpim"
    assert fake.history_calls[0]["oldest"] == "0"


def test_pull_is_idempotent_and_incremental(table, monkeypatch):
    _put_agent(table, "wilma", "UWILMABOT")
    fake = FakeAgentClient(history=[{"type": "message", "ts": "3.0", "user": "U1", "text": "a"}])
    monkeypatch.setattr(mpim, "agent_client", lambda _: fake)
    monkeypatch.setattr(mpim, "_resolve_name", lambda uid: "")

    assert mpim.pull_mpim_history("wilma", "C0G") == 1
    # Second pull starts after the stored message and re-seeing it writes nothing
    assert mpim.pull_mpim_history("wilma", "C0G") == 0
    assert fake.history_calls[1]["oldest"] == "3.0"


def test_check_messages_pulls_named_group_dm(table, monkeypatch):
    _put_agent(table, "wilma", "UWILMABOT")
    dynamo.register_channel("C0GROUP", "mpim")  # marked by a prior list_dms
    history = [
        {"type": "message", "ts": "5.0", "user": "U1", "text": "needs a reply"},
        {"type": "message", "ts": "4.0", "user": "UWILMABOT", "text": "my echo"},
    ]
    fake = FakeAgentClient(history=history)
    monkeypatch.setattr(mpim, "agent_client", lambda _: fake)
    monkeypatch.setattr(mpim, "_resolve_name", lambda uid: {"U1": "Emily"}.get(uid, ""))

    result = cm.check_messages("C0GROUP")

    assert result["ok"] is True
    # The group's history was pulled, and the agent's own echo is not returned
    assert [m["text"] for m in result["messages"]] == ["needs a reply"]
    assert fake.history_calls  # a pull happened


def test_check_messages_does_not_pull_unmarked_channel(table, monkeypatch):
    dynamo.register_channel("C0CHAN", "channel")
    fake = FakeAgentClient(history=[{"type": "message", "ts": "1.0", "user": "U1", "text": "x"}])
    monkeypatch.setattr(mpim, "agent_client", lambda _: fake)

    cm.check_messages("C0CHAN")

    assert fake.history_calls == []  # not marked mpim, so event-driven and never pulled


def test_empty_sweep_does_not_pull(table, monkeypatch):
    dynamo.register_channel("C0GROUP", "mpim")
    fake = FakeAgentClient(history=[{"type": "message", "ts": "1.0", "user": "U1"}])
    monkeypatch.setattr(mpim, "agent_client", lambda _: fake)

    cm.check_messages()  # no channel named

    assert fake.history_calls == []  # the sweep stays fast, group DMs pulled only when named

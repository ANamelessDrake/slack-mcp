import time

import boto3
import pytest
import tools.check_messages as cm
from moto import mock_aws
from sharedModules import dynamo, guardrails, identity


def test_token_map_local_mode_maps_to_default_agent():
    from auth import bearer

    bearer.token_map.cache_clear()
    try:
        assert bearer.token_map() == {"test-token": "wilma"}
        assert bearer._resolve("test-token") == "wilma"
        assert bearer._resolve("wrong") is None
    finally:
        bearer.token_map.cache_clear()


def test_token_map_reads_per_agent_secrets(monkeypatch):
    from auth import bearer

    monkeypatch.delenv("DEV_BEARER_TOKEN")
    monkeypatch.setenv("DEV_BEARER_TOKEN_SECRET", "Test-DevBearerToken")
    monkeypatch.setenv("MCP_TOKEN_SECRET_PREFIX", "Test-McpToken-")
    monkeypatch.setenv("AGENT_IDS", "claude,wilma")
    bearer.token_map.cache_clear()
    try:
        with mock_aws():
            sm = boto3.client("secretsmanager", region_name="us-east-1")
            sm.create_secret(Name="Test-DevBearerToken", SecretString="legacy-token")
            sm.create_secret(Name="Test-McpToken-claude", SecretString="claude-token")
            sm.create_secret(Name="Test-McpToken-wilma", SecretString="wilma-token")

            assert bearer._resolve("legacy-token") == "wilma"  # default agent
            assert bearer._resolve("claude-token") == "claude"
            assert bearer._resolve("wilma-token") == "wilma"
            assert bearer._resolve("nope") is None
    finally:
        bearer.token_map.cache_clear()


def test_identity_contextvar_overrides_env_default():
    assert identity.current_agent_id() == "wilma"  # env fallback
    identity.set_current_agent("claude")
    assert identity.current_agent_id() == "claude"
    identity.set_current_agent("")
    assert identity.current_agent_id() == "wilma"


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


def _seed(table, channel, ts, sender_type, agent_id="", thread_ts=""):
    table.put_item(
        Item={
            "PK": f"CH#{channel}",
            "SK": f"TS#{ts}",
            "channel": channel,
            "ts": ts,
            "thread_ts": thread_ts,
            "text": "x",
            "sender_type": sender_type,
            "agent_id": agent_id,
        }
    )


def test_turn_budget_blocks_after_consecutive_agent_messages(table, monkeypatch):
    monkeypatch.setenv("AGENT_TURN_BUDGET", "3")
    base = time.time() - 600
    for i in range(3):
        _seed(table, "C1", f"{base + i}", "agent", "wilma", thread_ts="1.0")

    veto = guardrails.agent_send_veto("C1", "1.0", "wilma")
    assert veto is not None and "Turn budget" in veto


def test_human_message_resets_turn_budget(table, monkeypatch):
    monkeypatch.setenv("AGENT_TURN_BUDGET", "3")
    base = time.time() - 600
    for i in range(3):
        _seed(table, "C1", f"{base + i}", "agent", "wilma", thread_ts="1.0")
    _seed(table, "C1", f"{base + 5}", "human", thread_ts="1.0")

    assert guardrails.agent_send_veto("C1", "1.0", "wilma") is None


def test_cooldown_between_different_agents(table, monkeypatch):
    monkeypatch.setenv("AGENT_COOLDOWN_SECONDS", "30")
    _seed(table, "C1", f"{time.time() - 1}", "agent", "claude")

    veto = guardrails.agent_send_veto("C1", "", "wilma")
    assert veto is not None and "Cooldown" in veto
    # The same agent continuing is not a cross-agent cooldown case
    assert guardrails.agent_send_veto("C1", "", "claude") is None


def test_thread_scope_does_not_count_channel_messages(table, monkeypatch):
    monkeypatch.setenv("AGENT_TURN_BUDGET", "2")
    base = time.time() - 600
    _seed(table, "C1", f"{base}", "agent", "wilma")          # top level
    _seed(table, "C1", f"{base + 1}", "agent", "wilma")      # top level
    _seed(table, "C1", f"{base + 2}", "agent", "wilma", thread_ts="9.0")

    assert guardrails.agent_send_veto("C1", "9.0", "wilma") is None
    veto = guardrails.agent_send_veto("C1", "", "wilma")
    assert veto is not None


def test_mentions_only_filters_without_consuming_unfiltered(table):
    table.put_item(Item={"PK": "CHANNELS", "SK": "CH#C1", "channel": "C1"})
    table.put_item(
        Item={
            "PK": "CH#C1", "SK": "TS#1.0", "channel": "C1", "ts": "1.0",
            "thread_ts": "", "text": "not for you", "sender_type": "human",
            "agent_id": "", "mentions_agents": [],
        }
    )
    table.put_item(
        Item={
            "PK": "CH#C1", "SK": "TS#2.0", "channel": "C1", "ts": "2.0",
            "thread_ts": "", "text": "hey wilma", "sender_type": "human",
            "agent_id": "", "mentions_agents": ["wilma"],
        }
    )

    result = cm.check_messages("C1", mentions_only=True)
    assert [m["text"] for m in result["messages"]] == ["hey wilma"]
    # The mentions view consumed its own read position
    assert cm.check_messages("C1", mentions_only=True)["messages"] == []
    # ...but the skipped non-mention message is NOT consumed for an unfiltered
    # check: per-filter cursors keep the views independent, so nothing is lost.
    assert [m["text"] for m in cm.check_messages("C1")["messages"]] == [
        "not for you",
        "hey wilma",
    ]

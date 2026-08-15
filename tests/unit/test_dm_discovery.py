"""Regression tests for the DM discoverability incident.

An agent DM'd a user via find_user's id, then polled check_messages with that
same U-prefixed id. It got {"ok": true, "messages": []} for an hour: success with
an empty list, indistinguishable from a quiet DM. These tests pin both halves of
the fix: a wrong target now fails loudly, and DMs are enumerable without having
already received a message in them.
"""

import boto3
import pytest
import tools.check_messages as cm
import tools.find_user as fu
import tools.list_dms as ld
import tools.read_history as rh
from moto import mock_aws
from sharedModules import conversations, dynamo


@pytest.fixture()
def table(monkeypatch):
    monkeypatch.setenv("MESSAGES_TABLE", "test-messages")
    dynamo.messages_table.cache_clear()
    conversations.dm_channel_for_user.cache_clear()
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
    conversations.dm_channel_for_user.cache_clear()


def _put_message(table, channel, ts, text="hi", user="U09GPLJ6W84", agent_id=""):
    table.put_item(
        Item={
            "PK": f"CH#{channel}",
            "SK": f"TS#{ts}",
            "channel": channel,
            "ts": ts,
            "thread_ts": "",
            "text": text,
            "user": user,
            "sender_type": "agent" if agent_id else "human",
            "agent_id": agent_id,
            "mentions": [],
        }
    )


class FakeAgentClient:
    """Stands in for the agent's Slack app: opens DMs and lists them."""

    def __init__(self, dm_map=None, ims=None):
        self.dm_map = dm_map or {}
        self.ims = ims or []
        self.open_calls = []

    def conversations_open(self, users):
        self.open_calls.append(users)
        return {"channel": {"id": self.dm_map[users]}}

    def conversations_list(self, **kwargs):
        assert kwargs.get("types") == "im", "DM listing must ask Slack for im types"
        return {"channels": self.ims, "response_metadata": {"next_cursor": ""}}


# --- Defect 1: silent wrong-target -----------------------------------------


def test_user_id_resolves_to_dm_instead_of_returning_empty(table, monkeypatch):
    """The exact incident: polling with a U-prefixed id must read that DM."""
    monkeypatch.setattr(
        conversations, "agent_client", lambda _: FakeAgentClient({"U09GPLJ6W84": "D0BHP613QBU"})
    )
    _put_message(table, "D0BHP613QBU", "1.0", text="the message that was missed")

    result = cm.check_messages("U09GPLJ6W84")

    assert result["ok"] is True
    assert [m["text"] for m in result["messages"]] == ["the message that was missed"]


def test_read_history_accepts_user_id(table, monkeypatch):
    monkeypatch.setattr(
        conversations, "agent_client", lambda _: FakeAgentClient({"U09GPLJ6W84": "D0BHP613QBU"})
    )
    _put_message(table, "D0BHP613QBU", "1.0", text="earlier context")

    result = rh.read_history("U09GPLJ6W84")

    assert result["ok"] is True
    assert [m["text"] for m in result["messages"]] == ["earlier context"]


@pytest.mark.parametrize("bad", ["emily", "mccabe", "#general", "!!!"])
def test_unusable_channel_fails_loudly_not_empty(table, bad):
    """Empty-success is unfalsifiable in a polling loop, so it must not happen."""
    for result in (cm.check_messages(bad), rh.read_history(bad)):
        assert result["ok"] is False
        assert "messages" not in result
        # The error has to name the forms that would have worked
        assert "C0123456789" in result["error"]
        assert "D0123456789" in result["error"]
        assert "U0123456789" in result["error"]


def test_one_bad_channel_fails_the_whole_call(table, monkeypatch):
    """Partial success would reintroduce the ambiguity this fix removes."""
    monkeypatch.setattr(conversations, "agent_client", lambda _: FakeAgentClient())
    _put_message(table, "C123", "1.0", text="real")

    result = cm.check_messages("C123,emily")

    assert result["ok"] is False


def test_conversation_ids_still_pass_through(table):
    _put_message(table, "C123", "1.0", text="in channel")
    _put_message(table, "D0DM1", "1.0", text="in dm")

    assert [m["text"] for m in cm.check_messages("C123")["messages"]] == ["in channel"]
    assert [m["text"] for m in cm.check_messages("D0DM1")["messages"]] == ["in dm"]


def test_empty_channel_still_sweeps_everything(table):
    for ch in ("C1", "D0DM1"):
        table.put_item(Item={"PK": "CHANNELS", "SK": f"CH#{ch}", "channel": ch})
    _put_message(table, "C1", "1.0", text="in channel")
    _put_message(table, "D0DM1", "1.0", text="in DM")

    result = cm.check_messages()

    assert sorted(m["text"] for m in result["messages"]) == ["in DM", "in channel"]


def test_dm_resolution_is_cached_across_polls(table, monkeypatch):
    """A monitoring loop must not hit conversations.open on every tick."""
    fake = FakeAgentClient({"U1": "D1"})
    monkeypatch.setattr(conversations, "agent_client", lambda _: fake)

    cm.check_messages("U1")
    cm.check_messages("U1")
    cm.check_messages("U1")

    assert len(fake.open_calls) == 1


# --- Defect 2: DMs are undiscoverable --------------------------------------


def test_list_dms_enumerates_without_prior_message(table, monkeypatch):
    """The old only-route to a DM id was receiving a message in it, which is
    circular when the problem is not receiving one."""
    fake = FakeAgentClient(
        ims=[
            {"id": "D0BHP613QBU", "user": "U09GPLJ6W84"},
            {"id": "D0QUIET", "user": "U2"},
            {"id": "D0GONE", "user": "U3", "is_user_deleted": True},
        ]
    )
    monkeypatch.setattr(conversations, "agent_client", lambda _: fake)
    monkeypatch.setattr(ld, "_user_name", lambda uid: {"U09GPLJ6W84": "Emily"}.get(uid, ""))
    _put_message(table, "D0BHP613QBU", "5.0", text="waiting for an answer")

    result = ld.list_dms()

    assert result["ok"] is True
    ids = [d["id"] for d in result["dms"]]
    assert "D0BHP613QBU" in ids
    assert "D0QUIET" in ids, "a DM with no stored messages must still be listed"
    assert "D0GONE" not in ids, "deactivated users are dropped"
    # Most recent activity first
    assert result["dms"][0]["id"] == "D0BHP613QBU"
    assert result["dms"][0]["user_name"] == "Emily"
    assert result["dms"][0]["unread_count"] == 1
    assert result["dms"][0]["last_activity_ts"] == "5.0"


def test_list_dms_unread_excludes_own_messages(table, monkeypatch):
    fake = FakeAgentClient(ims=[{"id": "D1", "user": "U1"}])
    monkeypatch.setattr(conversations, "agent_client", lambda _: fake)
    monkeypatch.setattr(ld, "_user_name", lambda uid: "")
    _put_message(table, "D1", "1.0", text="theirs")
    _put_message(table, "D1", "2.0", text="mine", agent_id="wilma")

    assert ld.list_dms()["dms"][0]["unread_count"] == 1


def test_list_dms_unread_respects_read_position(table, monkeypatch):
    fake = FakeAgentClient(ims=[{"id": "D1", "user": "U1"}])
    monkeypatch.setattr(conversations, "agent_client", lambda _: fake)
    monkeypatch.setattr(ld, "_user_name", lambda uid: "")
    _put_message(table, "D1", "1.0", text="theirs")

    assert ld.list_dms()["dms"][0]["unread_count"] == 1
    cm.check_messages("D1")  # consume it
    assert ld.list_dms()["dms"][0]["unread_count"] == 0


def test_find_user_reports_existing_dm_channel(monkeypatch):
    """Puts the DM id where an agent is already looking."""
    fake = FakeAgentClient(ims=[{"id": "D0BHP613QBU", "user": "U09GPLJ6W84"}])
    monkeypatch.setattr(conversations, "agent_client", lambda _: fake)
    monkeypatch.setattr(
        fu,
        "relay_client",
        lambda: type(
            "R",
            (),
            {
                "users_list": staticmethod(
                    lambda **kw: {
                        "members": [
                            {"id": "U09GPLJ6W84", "real_name": "Emily McCabe", "profile": {}},
                            {"id": "UNODM", "real_name": "Emily Other", "profile": {}},
                        ],
                        "response_metadata": {"next_cursor": ""},
                    }
                )
            },
        )(),
    )

    users = fu.find_user("emily")["users"]

    by_id = {u["id"]: u for u in users}
    assert by_id["U09GPLJ6W84"]["dm_channel_id"] == "D0BHP613QBU"
    # No DM yet means empty, not a fabricated id, and nothing was opened
    assert by_id["UNODM"]["dm_channel_id"] == ""
    assert fake.open_calls == []

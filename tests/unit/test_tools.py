import tools.list_channels as lc
import tools.read_thread as rt
import tools.send_message as sm
from slack_sdk.errors import SlackApiError


class FakeClient:
    """Captures the kwargs of the last API call and returns a canned response."""

    def __init__(self, response=None, error=None, dm_channel="D0NEW"):
        self.response = response or {}
        self.error = error
        self.dm_channel = dm_channel
        self.calls = []

    def _call(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if self.error:
            raise self.error
        return self.response

    def chat_postMessage(self, **kwargs):
        return self._call("chat.postMessage", **kwargs)

    def conversations_replies(self, **kwargs):
        return self._call("conversations.replies", **kwargs)

    def conversations_list(self, **kwargs):
        return self._call("conversations.list", **kwargs)

    def conversations_open(self, **kwargs):
        self.calls.append(("conversations.open", kwargs))
        return {"channel": {"id": self.dm_channel}}

    def users_list(self, **kwargs):
        return self._call("users.list", **kwargs)


def test_send_message_posts_and_returns_ts(monkeypatch):
    fake = FakeClient(response={"channel": "C123", "ts": "1700000000.000100"})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)

    result = sm.send_message("C123", "hello world")

    assert result == {
        "ok": True,
        "channel": "C123",
        "ts": "1700000000.000100",
        "agent_id": "wilma",
    }
    method, kwargs = fake.calls[0]
    assert kwargs["text"] == "hello world"
    assert "thread_ts" not in kwargs
    assert kwargs["metadata"]["event_payload"]["agent_id"] == "wilma"


def test_send_message_passes_thread_ts(monkeypatch):
    fake = FakeClient(response={"channel": "C123", "ts": "2.0"})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)

    sm.send_message("C123", "reply", thread_ts="1.0")

    _, kwargs = fake.calls[0]
    assert kwargs["thread_ts"] == "1.0"


def test_send_message_returns_slack_error(monkeypatch):
    error = SlackApiError("boom", {"error": "channel_not_found"})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: FakeClient(error=error))

    result = sm.send_message("C999", "hello")

    assert result["ok"] is False
    assert result["error"] == "channel_not_found"


def test_first_dm_by_user_id_adds_intro_note_and_registers(monkeypatch):
    fake = FakeClient(response={"channel": "D0NEW", "ts": "5.0"})
    registered = []
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)
    monkeypatch.setattr(sm, "channel_known", lambda ch: False)
    monkeypatch.setattr(sm, "register_channel", lambda ch, t="": registered.append(ch))

    result = sm.send_message("U0PERSON", "hi there")

    assert result["ok"] is True
    assert fake.calls[0][0] == "conversations.open"
    _, kwargs = fake.calls[1]
    assert kwargs["channel"] == "D0NEW"
    assert kwargs["text"].startswith("hi there")
    assert sm.DM_INTRO_NOTE.strip() in kwargs["text"]
    assert registered == ["D0NEW"]


def test_known_dm_gets_no_intro_note(monkeypatch):
    fake = FakeClient(response={"channel": "D0OLD", "ts": "6.0"})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)
    monkeypatch.setattr(sm, "channel_known", lambda ch: True)

    sm.send_message("D0OLD", "hi again")

    _, kwargs = fake.calls[0]
    assert kwargs["text"] == "hi again"


def test_channel_message_never_gets_intro_note(monkeypatch):
    fake = FakeClient(response={"channel": "C123", "ts": "7.0"})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)
    monkeypatch.setattr(
        sm, "channel_known", lambda ch: (_ for _ in ()).throw(AssertionError("not called"))
    )

    sm.send_message("C123", "channel note")

    _, kwargs = fake.calls[0]
    assert kwargs["text"] == "channel note"


def test_find_user_matches_and_paginates(monkeypatch):
    import tools.find_user as fu

    pages = [
        {
            "members": [
                {"id": "U1", "name": "susan", "real_name": "Susan Redman", "profile": {}},
                {"id": "U2", "name": "bob", "real_name": "Bob Jones", "profile": {}},
            ],
            "response_metadata": {"next_cursor": "page2"},
        },
        {
            "members": [
                {
                    "id": "U3",
                    "name": "ssmith",
                    "real_name": "Sam Smith",
                    "profile": {"display_name": "susan-backup"},
                },
                {"id": "U4", "name": "gone", "real_name": "Susan Old", "deleted": True},
            ],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    state = {"i": 0}

    class FakeRelay:
        def users_list(self, **kwargs):
            page = pages[state["i"]]
            state["i"] += 1
            return page

    monkeypatch.setattr(fu, "relay_client", lambda: FakeRelay())

    result = fu.find_user("susan")

    assert result["ok"] is True
    assert [u["id"] for u in result["users"]] == ["U1", "U3"]
    assert result["users"][0]["name"] == "Susan Redman"


def test_read_thread_maps_messages(monkeypatch):
    fake = FakeClient(
        response={
            "messages": [
                {"user": "U1", "text": "question", "ts": "1.0"},
                {"bot_id": "B1", "text": "answer", "ts": "2.0"},
            ]
        }
    )
    monkeypatch.setattr(rt, "relay_client", lambda: fake)

    result = rt.read_thread("C123", "1.0")

    assert result["ok"] is True
    assert result["messages"] == [
        {"user": "U1", "text": "question", "ts": "1.0"},
        {"user": "B1", "text": "answer", "ts": "2.0"},
    ]
    _, kwargs = fake.calls[0]
    assert kwargs["ts"] == "1.0"


def test_list_channels_maps_fields(monkeypatch):
    fake = FakeClient(
        response={
            "channels": [
                {"id": "C1", "name": "general", "is_member": True, "extra": "ignored"},
                {"id": "C2", "name": "secret-project", "is_member": True, "is_private": True},
                {"id": "C3", "name": "random"},
            ]
        }
    )
    monkeypatch.setattr(lc, "relay_client", lambda: fake)

    result = lc.list_channels()

    assert result["ok"] is True
    assert result["channels"] == [
        {"id": "C1", "name": "general", "is_member": True, "is_private": False},
        {"id": "C2", "name": "secret-project", "is_member": True, "is_private": True},
        {"id": "C3", "name": "random", "is_member": False, "is_private": False},
    ]
    _, kwargs = fake.calls[0]
    assert kwargs["types"] == "public_channel,private_channel"

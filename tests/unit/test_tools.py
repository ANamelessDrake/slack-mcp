import tools.list_channels as lc
import tools.read_thread as rt
import tools.send_message as sm
from slack_sdk.errors import SlackApiError


class FakeClient:
    """Captures the kwargs of the last API call and returns a canned response."""

    def __init__(self, response=None, error=None):
        self.response = response or {}
        self.error = error
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
                {"id": "C2", "name": "random"},
            ]
        }
    )
    monkeypatch.setattr(lc, "relay_client", lambda: fake)

    result = lc.list_channels()

    assert result["ok"] is True
    assert result["channels"] == [
        {"id": "C1", "name": "general", "is_member": True},
        {"id": "C2", "name": "random", "is_member": False},
    ]

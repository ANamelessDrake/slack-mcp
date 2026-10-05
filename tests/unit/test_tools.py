import base64

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
        self._upload_channel = None

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

    def files_upload_v2(self, **kwargs):
        self._upload_channel = kwargs.get("channel")
        return self._call("files.upload_v2", **kwargs)

    def files_getUploadURLExternal(self, **kwargs):
        self.calls.append(("files.getUploadURLExternal", kwargs))
        if self.error:
            raise self.error
        return {"ok": True, "upload_url": "https://files.slack.com/upload/x", "file_id": "F_NEW"}

    def files_completeUploadExternal(self, **kwargs):
        self._upload_channel = kwargs.get("channel_id")
        self.calls.append(("files.completeUploadExternal", kwargs))
        if self.error:
            raise self.error
        f = kwargs["files"][0]
        return {"ok": True, "files": [{"id": f["id"], "title": f.get("title", "")}]}

    def files_info(self, **kwargs):
        # The share ts resolves on the first poll; mirrors Slack populating it
        # shortly after the upload response, keyed by the channel just posted to.
        self.calls.append(("files.info", kwargs))
        ch = self._upload_channel or "C123"
        return {
            "ok": True,
            "file": {"id": kwargs.get("file", ""), "shares": {"public": {ch: [{"ts": "77.0"}]}}},
        }

    def users_list(self, **kwargs):
        return self._call("users.list", **kwargs)


def _no_veto(monkeypatch):
    monkeypatch.setattr(sm, "agent_send_veto", lambda *a: None)


def test_send_message_posts_and_returns_ts(monkeypatch):
    fake = FakeClient(response={"channel": "C123", "ts": "1700000000.000100"})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)
    _no_veto(monkeypatch)

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
    _no_veto(monkeypatch)
    fake = FakeClient(response={"channel": "C123", "ts": "2.0"})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)

    sm.send_message("C123", "reply", thread_ts="1.0")

    _, kwargs = fake.calls[0]
    assert kwargs["thread_ts"] == "1.0"


def test_send_message_returns_slack_error(monkeypatch):
    _no_veto(monkeypatch)
    error = SlackApiError("boom", {"error": "channel_not_found"})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: FakeClient(error=error))

    result = sm.send_message("C999", "hello")

    assert result["ok"] is False
    assert result["error"] == "channel_not_found"


def test_first_dm_by_user_id_adds_intro_note_and_registers(monkeypatch):
    _no_veto(monkeypatch)
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
    _no_veto(monkeypatch)
    fake = FakeClient(response={"channel": "D0OLD", "ts": "6.0"})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)
    monkeypatch.setattr(sm, "channel_known", lambda ch: True)

    sm.send_message("D0OLD", "hi again")

    _, kwargs = fake.calls[0]
    assert kwargs["text"] == "hi again"


def test_channel_message_never_gets_intro_note(monkeypatch):
    _no_veto(monkeypatch)
    fake = FakeClient(response={"channel": "C123", "ts": "7.0"})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)
    monkeypatch.setattr(
        sm, "channel_known", lambda ch: (_ for _ in ()).throw(AssertionError("not called"))
    )

    sm.send_message("C123", "channel note")

    _, kwargs = fake.calls[0]
    assert kwargs["text"] == "channel note"


def test_guardrail_veto_blocks_send(monkeypatch):
    fake = FakeClient(response={"channel": "C123", "ts": "8.0"})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)
    monkeypatch.setattr(sm, "agent_send_veto", lambda *a: "Turn budget reached")

    result = sm.send_message("C123", "one more thing")

    assert result == {"ok": False, "error": "Turn budget reached"}
    assert all(method != "chat.postMessage" for method, _ in fake.calls)


def test_file_upload_to_channel_uses_upload_and_returns_file_id(monkeypatch):
    _no_veto(monkeypatch)
    fake = FakeClient(
        response={
            "files": [
                {
                    "id": "F1",
                    "name": "report.pdf",
                    "shares": {"public": {"C123": [{"ts": "99.0"}]}},
                }
            ]
        }
    )
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)

    content = base64.b64encode(b"%PDF-1.4 fake").decode()
    result = sm.send_message(
        "C123", "here is the report", file_base64=content, file_name="report.pdf"
    )

    assert result == {
        "ok": True,
        "channel": "C123",
        "ts": "99.0",
        "agent_id": "wilma",
        "file_id": "F1",
        "file_name": "report.pdf",
    }
    method, kwargs = fake.calls[0]
    assert method == "files.upload_v2"
    assert kwargs["channel"] == "C123"
    assert kwargs["filename"] == "report.pdf"
    assert kwargs["file"] == b"%PDF-1.4 fake"
    assert kwargs["initial_comment"] == "here is the report"
    # A file post uses upload, never chat.postMessage
    assert all(method != "chat.postMessage" for method, _ in fake.calls)


def test_file_upload_passes_thread_ts(monkeypatch):
    _no_veto(monkeypatch)
    fake = FakeClient(response={"files": [{"id": "F1", "name": "a.txt"}]})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)

    content = base64.b64encode(b"hi").decode()
    sm.send_message("C123", "note", thread_ts="5.0", file_base64=content, file_name="a.txt")

    _, kwargs = fake.calls[0]
    assert kwargs["thread_ts"] == "5.0"


def test_file_upload_to_dm_resolves_user_then_uploads(monkeypatch):
    _no_veto(monkeypatch)
    fake = FakeClient(response={"files": [{"id": "F2", "name": "x.txt"}]}, dm_channel="D0NEW")
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)
    monkeypatch.setattr(sm, "channel_known", lambda ch: True)

    content = base64.b64encode(b"hi").decode()
    result = sm.send_message("U0PERSON", "for you", file_base64=content, file_name="x.txt")

    assert result["ok"] is True
    assert result["channel"] == "D0NEW"
    assert fake.calls[0][0] == "conversations.open"
    method, kwargs = fake.calls[1]
    assert method == "files.upload_v2"
    assert kwargs["channel"] == "D0NEW"


def test_file_upload_requires_file_name(monkeypatch):
    _no_veto(monkeypatch)
    fake = FakeClient(response={"files": [{"id": "F1"}]})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)

    content = base64.b64encode(b"data").decode()
    result = sm.send_message("C123", "x", file_base64=content)

    assert result["ok"] is False
    assert "file_name" in result["error"]
    assert fake.calls == []  # nothing sent to Slack


def test_file_upload_rejects_bad_base64(monkeypatch):
    _no_veto(monkeypatch)
    fake = FakeClient(response={})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)

    result = sm.send_message("C123", "x", file_base64="not!valid!base64", file_name="a.txt")

    assert result["ok"] is False
    assert "base64" in result["error"]
    assert fake.calls == []


def test_file_upload_rejects_oversize(monkeypatch):
    _no_veto(monkeypatch)
    fake = FakeClient(response={})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)
    monkeypatch.setattr(sm, "max_bytes", lambda: 4)

    content = base64.b64encode(b"way past the tiny cap").decode()
    result = sm.send_message("C123", "x", file_base64=content, file_name="big.bin")

    assert result["ok"] is False
    assert "limit" in result["error"]
    assert fake.calls == []


def test_file_upload_respects_guardrail_veto(monkeypatch):
    fake = FakeClient(response={"files": [{"id": "F1"}]})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)
    monkeypatch.setattr(sm, "agent_send_veto", lambda *a: "Turn budget reached")

    content = base64.b64encode(b"data").decode()
    result = sm.send_message("C123", "x", file_base64=content, file_name="a.txt")

    assert result == {"ok": False, "error": "Turn budget reached"}
    assert all(method != "files.upload_v2" for method, _ in fake.calls)


def test_file_upload_resolves_ts_via_files_info(monkeypatch):
    # The upload response omits the share, so the ts comes from a files.info poll
    _no_veto(monkeypatch)
    fake = FakeClient(response={"files": [{"id": "F1", "name": "report.pdf"}]})
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)

    content = base64.b64encode(b"data").decode()
    result = sm.send_message("C123", "x", file_base64=content, file_name="report.pdf")

    assert result["ok"] is True
    assert result["ts"] == "77.0"  # resolved, not left empty
    assert any(method == "files.info" for method, _ in fake.calls)


def test_start_file_upload_returns_url_and_id(monkeypatch):
    import tools.start_file_upload as sfu

    fake = FakeClient()
    monkeypatch.setattr(sfu, "agent_client", lambda agent_id: fake)

    result = sfu.start_file_upload("big.pdf", 9_000_000)

    assert result["ok"] is True
    assert result["upload_url"] == "https://files.slack.com/upload/x"
    assert result["file_id"] == "F_NEW"
    method, kwargs = fake.calls[0]
    assert method == "files.getUploadURLExternal"
    assert kwargs["filename"] == "big.pdf"
    assert kwargs["length"] == 9_000_000


def test_start_file_upload_validates_inputs(monkeypatch):
    import tools.start_file_upload as sfu

    monkeypatch.setattr(sfu, "agent_client", lambda agent_id: FakeClient())
    assert sfu.start_file_upload("", 10)["ok"] is False
    assert sfu.start_file_upload("big.pdf", 0)["ok"] is False


def test_send_message_completes_large_upload(monkeypatch):
    _no_veto(monkeypatch)
    fake = FakeClient()
    monkeypatch.setattr(sm, "agent_client", lambda agent_id: fake)

    result = sm.send_message("C123", "here it is", uploaded_file_id="F_BIG", file_name="big.pdf")

    assert result["ok"] is True
    assert result["channel"] == "C123"
    assert result["file_id"] == "F_BIG"
    assert result["ts"] == "77.0"
    methods = [m for m, _ in fake.calls]
    assert "files.completeUploadExternal" in methods
    assert "files.upload_v2" not in methods
    complete = next(kw for m, kw in fake.calls if m == "files.completeUploadExternal")
    assert complete["channel_id"] == "C123"
    assert complete["files"] == [{"id": "F_BIG", "title": "big.pdf"}]
    assert complete["initial_comment"] == "here it is"


def test_send_message_rejects_both_file_inputs():
    content = base64.b64encode(b"x").decode()
    result = sm.send_message("C123", "x", file_base64=content, uploaded_file_id="F1")
    assert result["ok"] is False
    assert "not both" in result["error"]


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

    # DM enrichment queries the agent app; give it no existing DMs
    class FakeAgent:
        def conversations_list(self, **kwargs):
            return {"channels": [], "response_metadata": {"next_cursor": ""}}

    from sharedModules import conversations

    monkeypatch.setattr(conversations, "agent_client", lambda _: FakeAgent())

    result = fu.find_user("susan")

    assert result["ok"] is True
    assert [u["id"] for u in result["users"]] == ["U1", "U3"]
    assert result["users"][0]["name"] == "Susan Redman"
    # No prior DM, so the field is present and empty, not fabricated
    assert result["users"][0]["dm_channel_id"] == ""


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

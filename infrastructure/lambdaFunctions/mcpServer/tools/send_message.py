import base64
import binascii
import logging
import os
import time

from sharedModules.dynamo import channel_known, register_channel
from sharedModules.files import max_bytes
from sharedModules.guardrails import agent_send_veto
from sharedModules.identity import current_agent_id
from sharedModules.slack import agent_client
from slack_sdk.errors import SlackApiError

log = logging.getLogger()

# files_upload_v2 returns before Slack reflects the share, so the upload
# response usually lacks the message ts. Poll files.info briefly to resolve it.
SHARE_TS_RETRIES = 6
SHARE_TS_DELAY = 0.25

# Appended automatically to the first message an agent ever sends into a DM
# conversation, so people know what to expect from their replies. This is a
# system property, not model etiquette, so it lives in the tool.
DM_INTRO_NOTE = (
    "\n\nNote: I am an AI agent and not always online. I will see your reply the "
    "next time I check this conversation, or right away if I am actively "
    "listening. When you reply, please include enough context about what you "
    "need: I may read your message in a fresh session without memory of this "
    "conversation."
)


def send_message(
    channel: str,
    text: str,
    thread_ts: str = "",
    file_base64: str = "",
    file_name: str = "",
    uploaded_file_id: str = "",
) -> dict:
    """Send a message to a Slack channel, a thread, or a person's DMs, with an
    optional file attachment.

    `channel` may be a channel ID (C0123456789, find one with list_channels), a
    DM conversation ID (D0123456789), or a user ID (U0123456789, find one with
    find_user) to direct-message that person. To reply inside an existing
    thread, pass that thread's `thread_ts` value; leave it empty to start a new
    message. Returns the new message's `ts`, which doubles as the `thread_ts`
    for reading or replying to its thread.

    Attaching a file, two ways depending on size:

    - Small file (up to about 4 MB): pass its bytes as `file_base64`
      (base64-encoded) with `file_name` (the display name including extension,
      like "report.pdf"). The bytes ride in this request, so this is capped
      around 4 MB; a larger file is rejected before it reaches the server. `text`
      becomes the comment posted with the file.
    - Larger file: do not use `file_base64`. First call `start_file_upload` to
      get an upload URL, POST the raw bytes to that URL yourself (that goes
      straight to Slack, with no size cap), then call send_message with
      `uploaded_file_id` set to the file id it returned. `text` becomes the
      comment, and `file_name` sets the title.

    Either way the response carries `file_id`; do not re-upload a file already in
    Slack. The server has no access to your disk, so for `file_base64` you read
    and encode the bytes yourself.

    To @mention a person inside the text, write <@THEIR_USER_ID>, for example
    "Hey <@U0123456789>, can you review this?" (get the ID from find_user).
    Plain text like "@Susan" does NOT notify anyone. The first message ever
    sent into a DM automatically includes a note telling the person when their
    replies will be seen.

    Posting a file into a channel needs the agent's Slack app to have the
    files:write scope and to be a member of that channel, the same membership
    that posting a message there already requires.
    """
    if uploaded_file_id and file_base64:
        return {
            "ok": False,
            "error": "pass either file_base64 (small files) or uploaded_file_id "
            "(large files via start_file_upload), not both",
        }

    agent_id = current_agent_id()
    client = agent_client(agent_id)
    target = channel

    file_bytes = b""
    upload_name = ""
    if file_base64:
        problem, file_bytes, upload_name = _prepare_upload(file_base64, file_name)
        if problem:
            return {"ok": False, "error": problem}

    try:
        # A user ID means "DM this person": resolve to their DM conversation
        if channel[:1] in ("U", "W") and channel[1:2].isalnum():
            target = client.conversations_open(users=channel)["channel"]["id"]

        veto = agent_send_veto(target, thread_ts, agent_id)
        if veto:
            return {"ok": False, "error": veto}

        first_dm_contact = target.startswith("D") and not thread_ts and not channel_known(target)
        body = text + DM_INTRO_NOTE if first_dm_contact else text

        if uploaded_file_id:
            # Large-file path: the bytes were already PUT to Slack via
            # start_file_upload, so just complete the share into this channel.
            resp = client.files_completeUploadExternal(
                files=[_completion_file(uploaded_file_id, file_name)],
                channel_id=target,
                **({"initial_comment": body} if body else {}),
                **({"thread_ts": thread_ts} if thread_ts else {}),
            )
            result = _upload_result(client, resp, target, agent_id, file_name)
            posted_channel = target
        elif file_bytes:
            resp = _upload(client, target, body, thread_ts, file_bytes, upload_name)
            result = _upload_result(client, resp, target, agent_id, upload_name)
            posted_channel = target
        else:
            kwargs = {
                "channel": target,
                "text": body,
                "metadata": {
                    "event_type": "agent_message",
                    "event_payload": {"agent_id": agent_id},
                },
            }
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            resp = client.chat_postMessage(**kwargs)
            result = {
                "ok": True,
                "channel": resp["channel"],
                "ts": resp["ts"],
                "agent_id": agent_id,
            }
            posted_channel = resp["channel"]
    except SlackApiError as e:
        # Upload and send failures are otherwise invisible: the tool returns the
        # error to the caller but nothing reaches the logs. Log it so a report of
        # "unreliable" uploads can be diagnosed from CloudWatch.
        log.warning("send_message failed (file=%s): %s", bool(file_bytes), _slack_error(e))
        return {"ok": False, "error": _slack_error(e)}

    # Register DMs at send time: agents without their own DM event subscription
    # would otherwise never get the conversation into the registry, and the
    # intro note would repeat forever.
    if first_dm_contact:
        register_channel(posted_channel, "im")

    return result


def _prepare_upload(file_base64: str, file_name: str) -> tuple[str, bytes, str]:
    """Validate and decode an outbound attachment.

    Returns (error, data, filename); error is "" on success. The bytes ride in
    the tool call itself, since the server cannot reach the agent's disk, so
    they arrive base64-encoded and are size-capped the same as inbound files.
    """
    name = file_name.strip()
    if not name:
        return ("file_name is required when file_base64 is set", b"", "")
    try:
        data = base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError):
        return ("file_base64 is not valid base64", b"", "")
    if not data:
        return ("file_base64 decoded to zero bytes", b"", "")
    limit = max_bytes()
    if len(data) > limit:
        return (
            f"file is {len(data) / 1048576:.1f} MB, over the {limit / 1048576:.0f} MB limit",
            b"",
            "",
        )
    # The agent picks the name, so any path components are just noise; keep the
    # basename so a stray path cannot change how Slack labels the file.
    return ("", data, os.path.basename(name) or name)


def _upload(client, channel: str, comment: str, thread_ts: str, data: bytes, filename: str):
    kwargs = {"channel": channel, "file": data, "filename": filename}
    if comment:
        kwargs["initial_comment"] = comment
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    return client.files_upload_v2(**kwargs)


def _completion_file(file_id: str, file_name: str) -> dict:
    """The files entry for completeUploadExternal. Title is optional; use the
    display name when one was given."""
    title = os.path.basename(file_name.strip()) or file_name.strip()
    return {"id": file_id, "title": title} if title else {"id": file_id}


def _upload_result(client, resp, channel: str, agent_id: str, fallback_name: str) -> dict:
    file_obj = _first_file(resp)
    return {
        "ok": True,
        "channel": channel,
        "ts": _resolve_share_ts(client, file_obj, channel),
        "agent_id": agent_id,
        "file_id": str(file_obj.get("id", "")),
        "file_name": str(file_obj.get("name") or fallback_name),
    }


def _resolve_share_ts(client, file_obj: dict, channel: str) -> str:
    """The ts of the file's message, resolved reliably.

    files_upload_v2 returns before the share is reflected, so its response
    usually omits the ts. When it does, poll files.info briefly until the share
    appears. The file posts regardless, so if the ts never shows, give up quietly
    and return "" rather than failing a send that actually succeeded."""
    ts = _share_ts(file_obj, channel)
    if ts:
        return ts
    file_id = str(file_obj.get("id", ""))
    if not file_id:
        return ""
    for _ in range(SHARE_TS_RETRIES):
        try:
            info = client.files_info(file=file_id)
        except SlackApiError:
            return ""
        ts = _share_ts(info.get("file") or {}, channel)
        if ts:
            return ts
        time.sleep(SHARE_TS_DELAY)
    return ""


def _first_file(resp) -> dict:
    files = resp.get("files")
    if files:
        return files[0] or {}
    return resp.get("file") or {}


def _share_ts(file_obj: dict, channel: str) -> str:
    """The message ts of a file share, best-effort.

    files_upload_v2 shares asynchronously, so the ts may not be present yet; an
    empty string just means "reply by reading the channel" rather than an error.
    """
    shares = (file_obj or {}).get("shares") or {}
    for visibility in ("public", "private"):
        entries = (shares.get(visibility) or {}).get(channel)
        if entries:
            return str(entries[0].get("ts", ""))
    return ""


def _slack_error(e: SlackApiError) -> str:
    try:
        return str(e.response["error"])
    except (TypeError, KeyError):
        return str(e)

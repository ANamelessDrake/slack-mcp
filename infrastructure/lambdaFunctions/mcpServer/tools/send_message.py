import base64
import binascii
import os

from sharedModules.dynamo import channel_known, register_channel
from sharedModules.files import max_bytes
from sharedModules.guardrails import agent_send_veto
from sharedModules.identity import current_agent_id
from sharedModules.slack import agent_client
from slack_sdk.errors import SlackApiError

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
) -> dict:
    """Send a message to a Slack channel, a thread, or a person's DMs, with an
    optional file attachment.

    `channel` may be a channel ID (C0123456789, find one with list_channels), a
    DM conversation ID (D0123456789), or a user ID (U0123456789, find one with
    find_user) to direct-message that person. To reply inside an existing
    thread, pass that thread's `thread_ts` value; leave it empty to start a new
    message. Returns the new message's `ts`, which doubles as the `thread_ts`
    for reading or replying to its thread.

    To attach a file, pass its bytes as `file_base64` (base64-encoded) together
    with `file_name` (the display name including extension, for example
    "report.pdf"); `text` becomes the comment posted with the file. This works
    the same in channels, threads, and DMs, and the size limit matches inbound
    files (max_file_download_mb). The server has no access to your disk, so the
    bytes must travel in the call itself: read the file, base64-encode it, and
    pass the string. Do not re-upload a file that is already in Slack; link to
    it instead. When a file is attached the response also carries `file_id`, and
    `ts` may be empty because Slack shares the file asynchronously.

    To @mention a person inside the text, write <@THEIR_USER_ID>, for example
    "Hey <@U0123456789>, can you review this?" (get the ID from find_user).
    Plain text like "@Susan" does NOT notify anyone. The first message ever
    sent into a DM automatically includes a note telling the person when their
    replies will be seen.

    Posting a file into a channel needs the agent's Slack app to have the
    files:write scope and to be a member of that channel, the same membership
    that posting a message there already requires.
    """
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

        if file_bytes:
            resp = _upload(client, target, body, thread_ts, file_bytes, upload_name)
            result = _upload_result(resp, target, agent_id, upload_name)
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


def _upload_result(resp, channel: str, agent_id: str, fallback_name: str) -> dict:
    file_obj = _first_file(resp)
    return {
        "ok": True,
        "channel": channel,
        "ts": _share_ts(file_obj, channel),
        "agent_id": agent_id,
        "file_id": str(file_obj.get("id", "")),
        "file_name": str(file_obj.get("name") or fallback_name),
    }


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

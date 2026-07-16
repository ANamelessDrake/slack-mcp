from sharedModules.dynamo import get_cursor, messages_after, set_cursor
from sharedModules.slack import default_agent_id, relay_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error


def check_messages(channel: str = "", limit: int = 20) -> dict:
    """Check for new Slack messages since your last check.

    Returns messages that arrived after your previous check_messages call and
    moves your read position forward. Pass a channel ID (like C0123456789) to
    check one channel, or leave it empty to check every channel the system is
    in. Messages you sent yourself are never included.
    """
    identity = default_agent_id()

    if channel:
        channels = [channel]
    else:
        try:
            resp = relay_client().conversations_list(
                types="public_channel", exclude_archived=True, limit=200
            )
        except SlackApiError as e:
            return {"ok": False, "error": _slack_error(e)}
        channels = [c["id"] for c in resp["channels"] if c.get("is_member")]

    new_messages = []
    for ch in channels:
        items = messages_after(ch, get_cursor(identity, ch), limit)
        if not items:
            continue
        # Advance past everything seen, including our own echoes
        set_cursor(identity, ch, items[-1]["ts"])
        for item in items:
            if item.get("agent_id", "") == identity:
                continue
            new_messages.append(
                {
                    "channel": item.get("channel", ch),
                    "ts": item.get("ts", ""),
                    "thread_ts": item.get("thread_ts", ""),
                    "text": item.get("text", ""),
                    "user": item.get("user", ""),
                    "sender_type": item.get("sender_type", ""),
                    "agent_id": item.get("agent_id", ""),
                    "mentions": list(item.get("mentions", [])),
                }
            )

    return {"ok": True, "messages": new_messages}

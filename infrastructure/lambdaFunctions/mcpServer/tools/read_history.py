from sharedModules.conversations import ChannelArgumentError, resolve_conversation
from sharedModules.dynamo import recent_messages
from sharedModules.identity import current_agent_id
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error


def read_history(channel: str, limit: int = 20) -> dict:
    """Read the recent message history of one channel or DM conversation.

    Use this to catch up on context, for example when a reply refers to an
    earlier conversation you do not remember. `channel` is required and must be
    a channel ID (C0123456789, from list_channels or find_channel), a DM ID
    (D0123456789, from list_dms), or a user ID (U0123456789, from find_user)
    which is resolved to that person's DM for you. Any other value returns an
    error, never an empty result.

    This reads one conversation at a time. Unlike check_messages there is no
    "all conversations" form, so to sweep everything call check_messages with an
    empty channel instead. Returns up to `limit` most recent messages, oldest
    first, including your own, and does not mark anything as read. Covers the
    deployment's retention window.
    """
    identity = current_agent_id()
    try:
        target = resolve_conversation(channel, identity)
    except ChannelArgumentError as e:
        return {"ok": False, "error": str(e)}
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    messages = [
        {
            "channel": item.get("channel", target),
            "ts": item.get("ts", ""),
            "thread_ts": item.get("thread_ts", ""),
            "text": item.get("text", ""),
            "user": item.get("user", ""),
            "user_name": item.get("user_name", ""),
            "sender_type": item.get("sender_type", ""),
            "agent_id": item.get("agent_id", ""),
            "files": list(item.get("files", [])),
        }
        for item in recent_messages(target, limit)
    ]
    return {"ok": True, "messages": messages}

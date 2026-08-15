from sharedModules.conversations import ChannelArgumentError, resolve_conversation
from sharedModules.dynamo import (
    cursor_scope,
    get_cursor,
    list_known_channels,
    messages_after,
    set_cursor,
)
from sharedModules.identity import current_agent_id
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error


def check_messages(
    channel: str = "", limit: int = 20, mentions_only: bool = False, from_user: str = ""
) -> dict:
    """Check for new Slack messages since your last check.

    Leave `channel` empty to check every conversation the system has seen,
    including DMs. That is the reliable way to notice a direct message and the
    right default for a monitoring loop. To narrow the check, `channel` may be
    one conversation ID or several separated by commas, each of which must be a
    channel ID (C0123456789, from list_channels or find_channel), a DM ID
    (D0123456789, from list_dms), or a user ID (U0123456789, from find_user)
    which is resolved to that person's DM for you. Any other value returns an
    error, never an empty result.

    Returns messages that arrived after your previous check_messages call and
    moves your read position forward. Messages you sent yourself are never
    included. Set mentions_only to true to receive only messages that @mention
    you; set from_user to a user ID to receive only that person's messages.

    Each distinct filter keeps its own read position. Checking with mentions_only
    or from_user only advances that filter's cursor, so a message it skips is
    still waiting the next time you check without the filter. It is never
    silently consumed.
    """
    identity = current_agent_id()
    requested = [c.strip() for c in channel.split(",") if c.strip()]

    try:
        channels = (
            [resolve_conversation(c, identity) for c in requested]
            if requested
            else list_known_channels()
        )
    except ChannelArgumentError as e:
        return {"ok": False, "error": str(e)}
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    new_messages = []
    for ch in channels:
        # Per-filter read position: skipping a message under one filter must not
        # consume it for a check under a different filter (or none).
        scope = cursor_scope(ch, mentions_only, from_user)
        items = messages_after(ch, get_cursor(identity, scope), limit)
        if not items:
            continue
        # Advance past everything seen in this view, including our own echoes
        set_cursor(identity, scope, items[-1]["ts"])
        for item in items:
            if item.get("agent_id", "") == identity:
                continue
            if mentions_only and identity not in item.get("mentions_agents", []):
                continue
            if from_user and item.get("user", "") != from_user:
                continue
            new_messages.append(
                {
                    "channel": item.get("channel", ch),
                    "ts": item.get("ts", ""),
                    "thread_ts": item.get("thread_ts", ""),
                    "text": item.get("text", ""),
                    "user": item.get("user", ""),
                    "user_name": item.get("user_name", ""),
                    "sender_type": item.get("sender_type", ""),
                    "agent_id": item.get("agent_id", ""),
                    "mentions": list(item.get("mentions", [])),
                    "mention_names": list(item.get("mention_names", [])),
                    "mentions_agents": list(item.get("mentions_agents", [])),
                    "files": list(item.get("files", [])),
                }
            )

    return {"ok": True, "messages": new_messages}

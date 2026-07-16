from sharedModules.dynamo import recent_messages


def read_history(channel: str, limit: int = 20) -> dict:
    """Read the recent message history of a channel or DM conversation.

    Use this to catch up on context, for example when a reply refers to an
    earlier conversation you do not remember. `channel` is a channel ID
    (C0123456789) or DM ID (D0123456789). Returns up to `limit` most recent
    messages, oldest first, including your own. Unlike check_messages, this
    does not mark anything as read. Covers messages from the last 30 days.
    """
    messages = [
        {
            "channel": item.get("channel", channel),
            "ts": item.get("ts", ""),
            "thread_ts": item.get("thread_ts", ""),
            "text": item.get("text", ""),
            "user": item.get("user", ""),
            "user_name": item.get("user_name", ""),
            "sender_type": item.get("sender_type", ""),
            "agent_id": item.get("agent_id", ""),
        }
        for item in recent_messages(channel, limit)
    ]
    return {"ok": True, "messages": messages}

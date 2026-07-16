from sharedModules.dynamo import get_cursor, list_known_channels, messages_after, set_cursor
from sharedModules.identity import current_agent_id


def check_messages(
    channel: str = "", limit: int = 20, mentions_only: bool = False, from_user: str = ""
) -> dict:
    """Check for new Slack messages since your last check.

    Returns messages that arrived after your previous check_messages call and
    moves your read position forward. `channel` may be one conversation ID
    (like C0123456789 or a DM id D0123456789), several separated by commas, or
    empty to check every conversation the system has seen. Messages you sent
    yourself are never included. Set mentions_only to true to receive only
    messages that @mention you; set from_user to a user ID (from find_user) to
    receive only that person's messages. Filtered-out messages are skipped for
    good, not saved for later.
    """
    identity = current_agent_id()
    requested = [c.strip() for c in channel.split(",") if c.strip()]
    channels = requested if requested else list_known_channels()

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

from sharedModules.dynamo import get_cursor, list_known_channels, messages_after, set_cursor
from sharedModules.identity import current_agent_id


def check_messages(channel: str = "", limit: int = 20, mentions_only: bool = False) -> dict:
    """Check for new Slack messages since your last check.

    Returns messages that arrived after your previous check_messages call and
    moves your read position forward. Pass a channel ID (like C0123456789, or a
    DM id like D0123456789) to check one conversation, or leave it empty to check
    every conversation the system has seen, including direct messages. Messages
    you sent yourself are never included. Set mentions_only to true to receive
    only messages that @mention you; other messages are then skipped for good,
    not saved for later.
    """
    identity = current_agent_id()
    channels = [channel] if channel else list_known_channels()

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
                }
            )

    return {"ok": True, "messages": new_messages}

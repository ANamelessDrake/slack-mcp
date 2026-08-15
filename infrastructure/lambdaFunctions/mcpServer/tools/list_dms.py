from sharedModules.conversations import list_dm_conversations
from sharedModules.dynamo import (
    cache_user,
    get_cached_user,
    get_cursor,
    messages_after,
    recent_messages,
)
from sharedModules.identity import current_agent_id
from sharedModules.slack import relay_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error

# How far past the read position we count before giving up on an exact number.
UNREAD_SCAN_LIMIT = 50


def list_dms() -> dict:
    """List your direct-message conversations and who each one is with.

    Use this whenever you need a DM's conversation ID (D0123456789) or want to
    see which people have messages waiting that you have not read. Slack's
    channel listing cannot enumerate DMs, so list_channels and find_channel will
    never show them: this is the only tool that does.

    Each entry has the DM's `id`, the other person's `user` ID and `user_name`,
    `last_activity_ts` (empty when nothing is stored for that conversation yet),
    and `unread_count`, meaning how many of their messages sit after your
    check_messages read position, counted up to 50. Most recent activity first.
    """
    agent_id = current_agent_id()
    try:
        conversations = list_dm_conversations(agent_id)
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    dms = []
    for conv in conversations:
        channel = conv["id"]
        pending = messages_after(channel, get_cursor(agent_id, channel), UNREAD_SCAN_LIMIT)
        latest = recent_messages(channel, 1)
        dms.append(
            {
                "id": channel,
                "user": conv["user"],
                "user_name": _user_name(conv["user"]),
                "last_activity_ts": latest[-1].get("ts", "") if latest else "",
                # Our own echoes are not unread mail
                "unread_count": sum(1 for m in pending if m.get("agent_id", "") != agent_id),
            }
        )

    # Quiet and never-used DMs sort to the bottom, where they belong
    dms.sort(key=lambda d: d["last_activity_ts"] or "", reverse=True)
    return {"ok": True, "dms": dms}


def _user_name(user_id: str) -> str:
    """Display name from the ingest-maintained cache, refetched once if cold."""
    if not user_id:
        return ""
    cached = get_cached_user(user_id)
    if cached and cached.get("name"):
        return str(cached["name"])
    try:
        info = relay_client().users_info(user=user_id)["user"]
    except SlackApiError:
        return ""
    profile = info.get("profile") or {}
    name = info.get("real_name") or profile.get("display_name") or info.get("name", "")
    if name:
        cache_user(user_id, name, bool(info.get("is_bot")))
    return name

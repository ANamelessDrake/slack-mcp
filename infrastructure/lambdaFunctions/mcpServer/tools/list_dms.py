from sharedModules.conversations import list_dm_conversations, mpim_member_ids
from sharedModules.dynamo import (
    cache_user,
    get_cached_user,
    get_cursor,
    messages_after,
    recent_messages,
    register_channel,
)
from sharedModules.identity import current_agent_id
from sharedModules.slack import relay_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error

# How far past the read position we count before giving up on an exact number.
UNREAD_SCAN_LIMIT = 50


def list_dms() -> dict:
    """List your direct and group message conversations and who is in each.

    Use this whenever you need a DM's conversation ID (D0123456789), a group DM's
    conversation ID, or want to see which conversations have messages waiting that
    you have not read. Slack's channel listing cannot enumerate DMs or group DMs,
    so list_channels and find_channel will never show them: this is the only tool
    that does.

    Each entry has the conversation `id`, `is_group_chat` (true for a multi-person
    group DM), `name` (the group DM's own name, like "JustWILMA2", empty for a
    one-to-one DM), and `members`, a list of {id, name} for the people in it. Do not
    infer the kind from the id prefix: a group DM's id may look like a channel id
    (it can start with C, not only G), so rely on `is_group_chat`. For a
    one-to-one DM, `user` and `user_name` name the other person and `members` has
    that one person; for a group DM, `user` is empty and `user_name` joins the
    members' names. Each entry also has `last_activity_ts`
    (empty when nothing is stored yet) and `unread_count`, how many messages from
    others sit after your check_messages read position, counted up to 50. Most
    recent activity first.
    """
    agent_id = current_agent_id()
    try:
        conversations = list_dm_conversations(agent_id)
    except SlackApiError as e:
        if e.response.get("error") == "missing_scope":
            return {
                "ok": False,
                "error": (
                    "list_dms needs the im:read scope (and mpim:read to include group "
                    "DMs) on this agent's Slack app. Add them under OAuth & Permissions "
                    "and reinstall the app."
                ),
            }
        return {"ok": False, "error": _slack_error(e)}

    dms = []
    for conv in conversations:
        channel = conv["id"]
        pending = messages_after(channel, get_cursor(agent_id, channel), UNREAD_SCAN_LIMIT)
        latest = recent_messages(channel, 1)
        if conv["is_group"]:
            # Mark it a group DM so check_messages knows to pull its history on
            # demand (Slack does not push group DMs to the event ingest).
            register_channel(channel, "mpim")
            members = _group_members(agent_id, channel)
            user = ""
            user_name = ", ".join(m["name"] for m in members if m["name"])
        elif conv["user"]:
            members = [{"id": conv["user"], "name": _user_name(conv["user"])}]
            user = conv["user"]
            user_name = members[0]["name"]
        else:
            members, user, user_name = [], "", ""
        dms.append(
            {
                "id": channel,
                "is_group_chat": conv["is_group"],
                "name": conv.get("name", ""),
                "user": user,
                "user_name": user_name,
                "members": members,
                "last_activity_ts": latest[-1].get("ts", "") if latest else "",
                # Our own echoes are not unread mail
                "unread_count": sum(1 for m in pending if m.get("agent_id", "") != agent_id),
            }
        )

    # Quiet and never-used conversations sort to the bottom, where they belong
    dms.sort(key=lambda d: d["last_activity_ts"] or "", reverse=True)
    return {"ok": True, "dms": dms}


def _group_members(agent_id: str, channel: str) -> list[dict]:
    """The {id, name} of everyone in a group DM. Best-effort: a listing should
    not fail wholesale because one group's membership could not be read."""
    try:
        ids = mpim_member_ids(agent_id, channel)
    except SlackApiError:
        return []
    return [{"id": uid, "name": _user_name(uid)} for uid in ids]


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

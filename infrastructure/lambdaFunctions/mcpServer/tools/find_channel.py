from sharedModules.conversations import list_dm_conversations
from sharedModules.dynamo import register_channel
from sharedModules.identity import current_agent_id
from sharedModules.slack import relay_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error

MAX_MATCHES = 10


def find_channel(name: str) -> dict:
    """Find a Slack channel's or group DM's ID from its name.

    Use this before send_message, check_messages, or read_canvas when you know a
    conversation by name (like "paymentproducts" or "JustWILMA2") but need its ID.
    Matches case-insensitively on any part of the name across public and private
    channels the system can see, and the group DMs (multi-person DMs) this agent
    is in. Returns up to 10 matches, each with `id`, `name`, `is_member`,
    `is_private`, and `is_group_chat`. Pass a match's `id` to other tools; for a
    group DM, check_messages on that id pulls its history.
    """
    query = name.strip().lstrip("#").lower()
    if not query:
        return {"ok": False, "error": "name must not be empty"}

    matches = []
    cursor = None
    try:
        while True:
            kwargs = {
                "types": "public_channel,private_channel",
                "exclude_archived": True,
                "limit": 200,
            }
            if cursor:
                kwargs["cursor"] = cursor
            resp = relay_client().conversations_list(**kwargs)
            for channel in resp["channels"]:
                if query in channel.get("name", "").lower():
                    matches.append(
                        {
                            "id": channel["id"],
                            "name": channel.get("name", ""),
                            "is_member": bool(channel.get("is_member")),
                            "is_private": bool(channel.get("is_private")),
                            "is_group_chat": False,
                        }
                    )
            cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor or len(matches) >= MAX_MATCHES:
                break
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    matches.extend(_matching_group_dms(query))

    # Exact name first, then the rest, so a precise query surfaces its conversation
    matches.sort(key=lambda c: (c["name"].lower() != query, c["name"].lower()))
    return {"ok": True, "channels": matches[:MAX_MATCHES]}


def _matching_group_dms(query: str) -> list[dict]:
    """Group DMs whose name matches, from the agent's own membership.

    The relay is not in most group DMs and its channel listing excludes them, so
    these come from the agent's token. Each is registered channel_type=mpim so a
    later check_messages knows to pull it. Best-effort: any failure here (missing
    scope, an agent with no token) still returns the channel matches, matching how
    find_user's DM enrichment degrades."""
    try:
        conversations = list_dm_conversations(current_agent_id())
    except Exception:
        return []
    found = []
    for conv in conversations:
        if conv.get("is_group") and query in (conv.get("name") or "").lower():
            register_channel(conv["id"], "mpim")
            found.append(
                {
                    "id": conv["id"],
                    "name": conv.get("name", ""),
                    "is_member": True,
                    "is_private": True,
                    "is_group_chat": True,
                }
            )
    return found

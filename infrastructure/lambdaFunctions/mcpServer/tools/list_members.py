from sharedModules.dynamo import cache_user, get_cached_user
from sharedModules.slack import relay_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error

MAX_MEMBERS = 200


def _describe(user_id: str) -> dict:
    """Resolve one member, reusing the name cache ingest already fills so a busy
    channel costs one Slack call per person per day rather than one per listing."""
    cached = get_cached_user(user_id)
    if cached and "is_bot" in cached:
        return {
            "id": user_id,
            "name": str(cached.get("name", "")),
            "is_bot": bool(cached.get("is_bot")),
        }

    try:
        info = relay_client().users_info(user=user_id)["user"]
    except SlackApiError:
        # A member we cannot resolve is still a member; report the id we have
        return {"id": user_id, "name": str((cached or {}).get("name", "")), "is_bot": False}

    profile = info.get("profile") or {}
    name = profile.get("display_name") or info.get("real_name") or info.get("name", "")
    is_bot = bool(info.get("is_bot"))
    cache_user(user_id, name, is_bot)
    return {"id": user_id, "name": name, "is_bot": is_bot}


def list_members(channel: str) -> dict:
    """List the people and agents in a Slack channel.

    Use this to see who is in a conversation before addressing someone, for
    example to @mention a person you have not heard from yet. `channel` is a
    channel ID (C0123456789) from list_channels, or a DM id. Returns each
    member's `id`, `name`, and whether it `is_bot` (agents and apps are bots).
    To @mention someone in a message, write <@THEIR_ID> in the text.
    """
    try:
        resp = relay_client().conversations_members(channel=channel, limit=MAX_MEMBERS)
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    return {
        "ok": True,
        "channel": channel,
        "members": [_describe(uid) for uid in resp.get("members", [])],
    }

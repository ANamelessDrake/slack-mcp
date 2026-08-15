from sharedModules.conversations import user_dm_index
from sharedModules.identity import current_agent_id
from sharedModules.slack import relay_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error

MAX_MATCHES = 10


def find_user(name: str) -> dict:
    """Find a Slack user's ID from their name, and their DM conversation.

    Use this when you want to reach a person and only know their name. Matches
    username, real name, and display name, case-insensitive, returning up to 10
    users.

    Pass a match's `id` as the `channel` argument of send_message to DM them, or
    of check_messages or read_history to read that DM. `dm_channel_id` is that
    DM conversation's own ID (D0123456789) when one already exists, and is empty
    when you have never exchanged messages with the person; send_message creates
    it on first contact. To browse existing DMs instead of searching by name,
    use list_dms.
    """
    query = name.strip().lower()
    if not query:
        return {"ok": False, "error": "name must not be empty"}

    # Existing DMs only: looking someone up must not open a conversation with them.
    # The DM id is an enrichment, not this tool's job, so if the listing fails
    # (permissions, transient API error) fall back to blank and still return the
    # user matches. Empty here is unambiguous: send_message resolves a U-id on its
    # own, so a missing dm_channel_id never blocks reaching the person.
    try:
        dm_index = user_dm_index(current_agent_id())
    except Exception:
        dm_index = {}

    matches = []
    cursor = None
    try:
        while True:
            kwargs = {"limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            resp = relay_client().users_list(**kwargs)
            for user in resp["members"]:
                if user.get("deleted"):
                    continue
                profile = user.get("profile") or {}
                candidates = (
                    user.get("name", ""),
                    user.get("real_name", ""),
                    profile.get("display_name", ""),
                )
                if any(query in c.lower() for c in candidates if c):
                    matches.append(
                        {
                            "id": user["id"],
                            "name": user.get("real_name") or user.get("name", ""),
                            "display_name": profile.get("display_name", ""),
                            "is_bot": bool(user.get("is_bot")),
                            "dm_channel_id": dm_index.get(user["id"], ""),
                        }
                    )
            cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor or len(matches) >= MAX_MATCHES:
                break
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    return {"ok": True, "users": matches[:MAX_MATCHES]}

from sharedModules.slack import relay_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error

MAX_MATCHES = 10


def find_user(name: str) -> dict:
    """Find a Slack user's ID from their name.

    Use this before send_message when you want to direct-message a person and
    only know their name. Matches username, real name, and display name,
    case-insensitive. Returns up to 10 matching users; pass a match's `id` as
    the `channel` argument of send_message to DM them.
    """
    query = name.strip().lower()
    if not query:
        return {"ok": False, "error": "name must not be empty"}

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
                        }
                    )
            cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor or len(matches) >= MAX_MATCHES:
                break
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    return {"ok": True, "users": matches[:MAX_MATCHES]}

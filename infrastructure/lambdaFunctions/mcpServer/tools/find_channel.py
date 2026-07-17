from sharedModules.slack import relay_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error

MAX_MATCHES = 10


def find_channel(name: str) -> dict:
    """Find a Slack channel's ID from its name.

    Use this before send_message or read_canvas when you know a channel by name
    (like "paymentproducts" or "general") but need its ID. Matches
    case-insensitively on any part of the name, public and private channels the
    system can see. Returns up to 10 matches, each with `id`, `name`,
    `is_member`, and `is_private`. Pass a match's `id` to other tools.
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
                        }
                    )
            cursor = (resp.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor or len(matches) >= MAX_MATCHES:
                break
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    # Exact name first, then the rest, so a precise query surfaces its channel
    matches.sort(key=lambda c: (c["name"].lower() != query, c["name"].lower()))
    return {"ok": True, "channels": matches[:MAX_MATCHES]}

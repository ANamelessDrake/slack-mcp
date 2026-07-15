from sharedModules.slack import relay_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error


def list_channels() -> dict:
    """List the public Slack channels this server can see.

    Use this first to find the channel ID (a string like C0123456789) that
    send_message and read_thread require. Only channels where `is_member` is true
    can receive messages.
    """
    try:
        resp = relay_client().conversations_list(
            types="public_channel", exclude_archived=True, limit=200
        )
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    channels = [
        {
            "id": c["id"],
            "name": c.get("name", ""),
            "is_member": bool(c.get("is_member")),
        }
        for c in resp["channels"]
    ]
    return {"ok": True, "channels": channels}

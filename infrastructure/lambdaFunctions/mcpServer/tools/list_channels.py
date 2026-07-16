from sharedModules.slack import relay_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error


def list_channels() -> dict:
    """List the Slack channels this server can see, public and private.

    Use this first to find the channel ID (a string like C0123456789) that
    send_message and read_thread require. Only channels where `is_member` is
    true can receive messages. Private channels appear only if the system has
    been invited to them (`is_private` true).
    """
    try:
        resp = relay_client().conversations_list(
            types="public_channel,private_channel", exclude_archived=True, limit=200
        )
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    channels = [
        {
            "id": c["id"],
            "name": c.get("name", ""),
            "is_member": bool(c.get("is_member")),
            "is_private": bool(c.get("is_private")),
        }
        for c in resp["channels"]
    ]
    return {"ok": True, "channels": channels}

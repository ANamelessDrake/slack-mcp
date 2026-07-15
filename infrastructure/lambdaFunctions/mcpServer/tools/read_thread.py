from sharedModules.slack import relay_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error


def read_thread(channel: str, thread_ts: str, limit: int = 50) -> dict:
    """Read the replies in one Slack thread.

    Use this after send_message to see responses to that message. `channel` is the
    channel ID and `thread_ts` is the `ts` value that send_message returned.
    Returns messages oldest first, including the thread's first message.
    """
    try:
        resp = relay_client().conversations_replies(
            channel=channel, ts=thread_ts, limit=limit
        )
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    messages = [
        {
            "user": m.get("user") or m.get("bot_id") or "",
            "text": m.get("text", ""),
            "ts": m.get("ts", ""),
        }
        for m in resp["messages"]
    ]
    return {"ok": True, "messages": messages}

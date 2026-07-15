from sharedModules.slack import agent_client, default_agent_id
from slack_sdk.errors import SlackApiError


def send_message(channel: str, text: str, thread_ts: str = "") -> dict:
    """Send a message to a Slack channel, or reply to a thread.

    Use this to post a message into Slack. `channel` is a channel ID such as
    C0123456789; call list_channels first to find it. To reply inside an existing
    thread, pass that thread's `thread_ts` value; leave it empty to start a new
    message in the channel. Returns the new message's `ts`, which doubles as the
    `thread_ts` for reading or replying to its thread.
    """
    agent_id = default_agent_id()
    kwargs = {
        "channel": channel,
        "text": text,
        "metadata": {
            "event_type": "agent_message",
            "event_payload": {"agent_id": agent_id},
        },
    }
    if thread_ts:
        kwargs["thread_ts"] = thread_ts

    try:
        resp = agent_client(agent_id).chat_postMessage(**kwargs)
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    return {
        "ok": True,
        "channel": resp["channel"],
        "ts": resp["ts"],
        "agent_id": agent_id,
    }


def _slack_error(e: SlackApiError) -> str:
    try:
        return str(e.response["error"])
    except (TypeError, KeyError):
        return str(e)

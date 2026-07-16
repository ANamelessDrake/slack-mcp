from sharedModules.dynamo import channel_known, register_channel
from sharedModules.guardrails import agent_send_veto
from sharedModules.identity import current_agent_id
from sharedModules.slack import agent_client
from slack_sdk.errors import SlackApiError

# Appended automatically to the first message an agent ever sends into a DM
# conversation, so people know what to expect from their replies. This is a
# system property, not model etiquette, so it lives in the tool.
DM_INTRO_NOTE = (
    "\n\nNote: I am an AI agent and not always online. I will see your reply the "
    "next time I check this conversation, or right away if I am actively "
    "listening. When you reply, please include enough context about what you "
    "need: I may read your message in a fresh session without memory of this "
    "conversation."
)


def send_message(channel: str, text: str, thread_ts: str = "") -> dict:
    """Send a message to a Slack channel, a thread, or a person's DMs.

    `channel` may be a channel ID (C0123456789, find one with list_channels), a
    DM conversation ID (D0123456789), or a user ID (U0123456789, find one with
    find_user) to direct-message that person. To reply inside an existing
    thread, pass that thread's `thread_ts` value; leave it empty to start a new
    message. Returns the new message's `ts`, which doubles as the `thread_ts`
    for reading or replying to its thread.

    To @mention a person inside the text, write <@THEIR_USER_ID>, for example
    "Hey <@U0123456789>, can you review this?" (get the ID from find_user).
    Plain text like "@Susan" does NOT notify anyone. The first message ever
    sent into a DM automatically includes a note telling the person when their
    replies will be seen.
    """
    agent_id = current_agent_id()
    client = agent_client(agent_id)
    target = channel

    try:
        # A user ID means "DM this person": resolve to their DM conversation
        if channel[:1] in ("U", "W") and channel[1:2].isalnum():
            target = client.conversations_open(users=channel)["channel"]["id"]

        veto = agent_send_veto(target, thread_ts, agent_id)
        if veto:
            return {"ok": False, "error": veto}

        first_dm_contact = (
            target.startswith("D") and not thread_ts and not channel_known(target)
        )
        body = text + DM_INTRO_NOTE if first_dm_contact else text

        kwargs = {
            "channel": target,
            "text": body,
            "metadata": {
                "event_type": "agent_message",
                "event_payload": {"agent_id": agent_id},
            },
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        resp = client.chat_postMessage(**kwargs)
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    # Register DMs at send time: agents without their own DM event subscription
    # would otherwise never get the conversation into the registry, and the
    # intro note would repeat forever.
    if first_dm_contact:
        register_channel(resp["channel"], "im")

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

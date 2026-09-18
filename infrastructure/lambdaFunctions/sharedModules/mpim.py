"""On-demand history pull for group DMs (multi-person DMs).

Slack does not reliably deliver message.mpim events to bots (the event cannot
even be subscribed on a granular-scope app), so group DMs never reach the event
ingest. Their history is still readable by pull: the agent is a member and has
mpim:history. This module fetches messages newer than what is stored and writes
them into the same inbox table the event ingest writes to, so check_messages and
wait_for_messages surface group-DM messages like any other. The pull is
triggered on demand, when check_messages is called with a group DM's id, never
on a schedule, so there is no idle cost and the caller controls timing.

The one inherent limitation: there is no instant wake for a group DM, because
Slack pushes nothing. A group DM is seen when it is polled, not the moment
someone posts.
"""

import os
import re
import time

from boto3.dynamodb.conditions import Key
from slack_sdk.errors import SlackApiError

from sharedModules.dynamo import (
    cache_user,
    get_cached_user,
    messages_table,
    register_channel,
)
from sharedModules.slack import agent_client, relay_client

MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")
# History rows that are edits, deletions, or joins, not standalone messages.
IGNORED_SUBTYPES = {"message_changed", "message_deleted", "channel_join", "channel_leave"}
HISTORY_LIMIT = 100


def pull_mpim_history(agent_id: str, channel: str) -> int:
    """Fetch group-DM messages newer than what is stored and write them.

    Returns the number written. Idempotent: writes use a conditional put on
    (channel, ts), so re-pulling the boundary message is a no-op and calling this
    on every check is safe. Uses the agent's own token, since the agent is a
    member of its group DMs and the relay is not.
    """
    oldest = _latest_stored_ts(channel)
    try:
        resp = agent_client(agent_id).conversations_history(
            channel=channel, oldest=oldest, inclusive=False, limit=HISTORY_LIMIT
        )
    except SlackApiError:
        return 0

    agent_map = _agent_by_bot_user()
    ttl = _message_ttl()
    table = messages_table()
    written = 0
    # history is newest-first; write oldest-first so the stored maximum ts
    # advances cleanly and the next pull's `oldest` is correct.
    for msg in reversed(resp.get("messages", [])):
        if msg.get("type") != "message" or msg.get("subtype") in IGNORED_SUBTYPES:
            continue
        ts = msg.get("ts", "")
        if not ts:
            continue
        poster = msg.get("user", "")
        sender = poster or msg.get("bot_id", "") or ""
        agent_of = agent_map.get(poster, "")
        text = msg.get("text", "")
        mentions = MENTION_RE.findall(text)
        item = {
            "PK": f"CH#{channel}",
            "SK": f"TS#{ts}",
            "channel": channel,
            "ts": ts,
            "thread_ts": msg.get("thread_ts", ""),
            "text": text,
            "user": sender,
            "user_name": _resolve_name(sender),
            "sender_type": "agent" if agent_of else ("bot" if msg.get("bot_id") else "human"),
            "agent_id": agent_of,
            "mentions": mentions,
            "mention_names": [_resolve_name(m) for m in mentions],
            "mentions_agents": [agent_map[u] for u in mentions if u in agent_map],
            "slack_event_id": "",
            "files": [
                {
                    "id": f["id"],
                    "name": f.get("name", ""),
                    "mimetype": f.get("mimetype", ""),
                    "size": int(f.get("size", 0) or 0),
                }
                for f in (msg.get("files") or [])
                if f.get("id")
            ],
        }
        if ttl is not None:
            item["ttl"] = ttl
        try:
            table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
            written += 1
        except table.meta.client.exceptions.ConditionalCheckFailedException:
            pass  # already stored by an earlier pull: idempotent

    # Register so the id resolves as a known mpim (later checks skip the info
    # call) and the sweep can surface what has been pulled.
    register_channel(channel, "mpim")
    return written


def _latest_stored_ts(channel: str) -> str:
    resp = messages_table().query(
        KeyConditionExpression=Key("PK").eq(f"CH#{channel}") & Key("SK").begins_with("TS#"),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0]["ts"] if items else "0"


def _agent_by_bot_user() -> dict:
    """bot_user_id -> agent_id, so an agent's own group-DM posts are attributed
    (and skipped as echoes on read), matching how ingest attributes events."""
    resp = messages_table().query(KeyConditionExpression=Key("PK").eq("AGENTS"))
    return {
        item["bot_user_id"]: item["agent_id"]
        for item in resp.get("Items", [])
        if item.get("bot_user_id")
    }


def _message_ttl() -> int | None:
    days = int(os.environ.get("MESSAGE_TTL_DAYS", "30"))
    return int(time.time()) + days * 86400 if days > 0 else None


def _resolve_name(user_id: str) -> str:
    if not user_id or not user_id.startswith(("U", "W")):
        return ""
    cached = get_cached_user(user_id)
    if cached and cached.get("name"):
        return str(cached["name"])
    try:
        info = relay_client().users_info(user=user_id)["user"]
    except SlackApiError:
        return ""
    profile = info.get("profile") or {}
    name = info.get("real_name") or profile.get("display_name") or info.get("name", "")
    if name:
        cache_user(user_id, name, bool(info.get("is_bot")))
    return name

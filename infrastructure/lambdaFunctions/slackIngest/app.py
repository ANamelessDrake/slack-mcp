"""Slack Events API receiver.

Verifies the request signature, answers the URL-verification handshake, and
stores message events in the inbox table. The durable DynamoDB write is the
delivery guarantee; real-time publish (AppSync Events) arrives in milestone 3
and happens after the write (DESIGN.md section 6).

Self-contained on purpose: stdlib + boto3 only, so the asset deploys without a
bundling step.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.request
from functools import lru_cache

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

# Message subtypes that are edits, deletions, or channel noise, not new messages
IGNORED_SUBTYPES = {
    "message_changed",
    "message_deleted",
    "message_replied",
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
}

SIGNATURE_WINDOW_SECONDS = 300
USER_CACHE_TTL_SECONDS = 24 * 3600


def _message_expiry() -> int | None:
    """Epoch TTL for message items, or None to keep them forever
    (message_retention_days = 0 in the environment config)."""
    days = int(os.environ.get("MESSAGE_TTL_DAYS", "30"))
    if days <= 0:
        return None
    return int(time.time()) + days * 24 * 3600

_TABLE = None
_USER_NAMES: dict[str, str] = {}
_AGENT_BY_BOT_USER: dict[str, str] | None = None


def _table():
    global _TABLE
    if _TABLE is None:
        _TABLE = boto3.resource("dynamodb").Table(os.environ["MESSAGES_TABLE"])
    return _TABLE


@lru_cache(maxsize=1)
def _signing_secrets() -> tuple[str, ...]:
    direct = os.environ.get("SLACK_SIGNING_SECRET")
    if direct:
        return (direct,)
    client = boto3.client("secretsmanager")
    names = os.environ["SIGNING_SECRET_NAMES"].split(",")
    return tuple(
        client.get_secret_value(SecretId=name.strip())["SecretString"] for name in names
    )


@lru_cache(maxsize=1)
def _relay_token() -> str:
    direct = os.environ.get("RELAY_BOT_TOKEN")
    if direct:
        return direct
    client = boto3.client("secretsmanager")
    return client.get_secret_value(SecretId=os.environ["RELAY_BOT_TOKEN_SECRET"])["SecretString"]


def _lookup_user(user_id: str) -> str:
    """users.info via the relay token. Returns the person's name or ''."""
    req = urllib.request.Request(
        f"https://slack.com/api/users.info?user={user_id}",
        headers={"Authorization": f"Bearer {_relay_token()}"},
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        data = json.loads(resp.read().decode())
    if not data.get("ok"):
        return ""
    user = data.get("user") or {}
    profile = user.get("profile") or {}
    return profile.get("display_name") or user.get("real_name") or user.get("name") or ""


def _resolve_user_name(user_id: str) -> str:
    """Resolve a Slack user ID to a name: identity is infrastructure, so it is
    stamped onto messages here rather than requested by LLMs (one cached lookup
    per person, and every MCP client sees the name with zero extra calls)."""
    if not user_id or not user_id.startswith(("U", "W")):
        return ""
    if user_id in _USER_NAMES:
        return _USER_NAMES[user_id]

    cached = _table().get_item(Key={"PK": f"USER#{user_id}", "SK": "META"}).get("Item")
    if cached:
        name = cached.get("name", "")
    else:
        try:
            name = _lookup_user(user_id)
        except Exception:
            log.warning("users.info failed for %s", user_id, exc_info=True)
            return ""
        _table().put_item(
            Item={
                "PK": f"USER#{user_id}",
                "SK": "META",
                "name": name,
                "ttl": int(time.time()) + USER_CACHE_TTL_SECONDS,
            }
        )
    _USER_NAMES[user_id] = name
    return name


def _agent_mention_map() -> dict[str, str]:
    """bot_user_id -> agent_id, from the AGENTS registry (scripts/admin.py).
    Lets @mentions of agent bots route to agent identities. Cached per
    container; registering a new agent picks up on container recycle."""
    global _AGENT_BY_BOT_USER
    if _AGENT_BY_BOT_USER is None:
        from boto3.dynamodb.conditions import Key

        resp = _table().query(KeyConditionExpression=Key("PK").eq("AGENTS"))
        _AGENT_BY_BOT_USER = {
            item["bot_user_id"]: item["agent_id"]
            for item in resp.get("Items", [])
            if item.get("bot_user_id")
        }
    return _AGENT_BY_BOT_USER


def _valid_signature(headers: dict, body: str) -> bool:
    """Accept a signature from any known app: the relay, or an agent app whose
    DM events (message.im) also point here. Each app signs with its own secret."""
    ts = headers.get("x-slack-request-timestamp", "")
    sig = headers.get("x-slack-signature", "")
    if not ts or not sig:
        return False
    try:
        if abs(time.time() - int(ts)) > SIGNATURE_WINDOW_SECONDS:
            return False
    except ValueError:
        return False
    base = f"v0:{ts}:{body}".encode()
    for secret in _signing_secrets():
        expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, sig):
            return True
    return False


def _response(status: int, body: str = "") -> dict:
    return {"statusCode": status, "body": body}


def _publish(message: dict) -> None:
    """Best-effort push to AppSync Events for any live wait_for_messages session.

    Runs only after the durable DynamoDB write; failure here is logged and
    swallowed because offline delivery via the inbox is unaffected.
    """
    endpoint = os.environ.get("EVENTS_HTTP_ENDPOINT", "")
    if not endpoint:
        return
    body = json.dumps(
        {
            "channel": f"slack/messages/{message['channel']}",
            "events": [json.dumps(message)],
        }
    ).encode()
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": os.environ["EVENTS_API_KEY"],
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=3)


def handler(event, _context):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    if not _valid_signature(headers, raw):
        return _response(401, "invalid signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _response(400, "bad request")

    if payload.get("type") == "url_verification":
        return _response(200, payload.get("challenge", ""))
    if payload.get("type") != "event_callback":
        return _response(200, "ignored")

    ev = payload.get("event") or {}
    if ev.get("type") != "message" or ev.get("subtype") in IGNORED_SUBTYPES:
        return _response(200, "ignored")

    channel = ev.get("channel", "")
    ts = ev.get("ts", "")
    if not channel or not ts:
        return _response(200, "ignored")

    metadata = ev.get("metadata") or {}
    agent_payload = (
        metadata.get("event_payload") or {}
        if metadata.get("event_type") == "agent_message"
        else {}
    )
    agent_id = str(agent_payload.get("agent_id", ""))
    text = ev.get("text", "")
    sender = ev.get("user") or ev.get("bot_id") or ""
    mentions = re.findall(r"<@([A-Z0-9]+)>", text)

    item = {
        "PK": f"CH#{channel}",
        "SK": f"TS#{ts}",
        "channel": channel,
        "ts": ts,
        "thread_ts": ev.get("thread_ts", ""),
        "text": text,
        "user": sender,
        "user_name": _resolve_user_name(sender),
        "sender_type": "agent" if agent_id else ("bot" if ev.get("bot_id") else "human"),
        "agent_id": agent_id,
        "mentions": mentions,
        "mention_names": [_resolve_user_name(m) for m in mentions],
        "mentions_agents": [
            agent for uid in mentions if (agent := _agent_mention_map().get(uid))
        ],
        "slack_event_id": payload.get("event_id", ""),
    }
    expiry = _message_expiry()
    if expiry is not None:
        item["ttl"] = expiry

    # Dedupe on (channel, ts): idempotent against Slack's delivery retries and,
    # later, against multiple subscribing apps (DESIGN.md section 3).
    try:
        _table().put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
        log.info("stored message %s %s (sender=%s)", channel, ts, item["sender_type"])
    except _table().meta.client.exceptions.ConditionalCheckFailedException:
        log.info("duplicate message %s %s ignored", channel, ts)
        return _response(200, "ok")

    # Channel registry: lets check_messages sweep every conversation the system
    # has seen, including agent DMs that Slack's channel-listing APIs cannot show.
    _table().put_item(
        Item={
            "PK": "CHANNELS",
            "SK": f"CH#{channel}",
            "channel": channel,
            "channel_type": ev.get("channel_type", ""),
            "last_ts": ts,
        }
    )

    try:
        _publish({k: v for k, v in item.items() if k not in ("PK", "SK", "ttl")})
    except Exception:
        log.warning("events publish failed for %s %s", channel, ts, exc_info=True)

    return _response(200, "ok")

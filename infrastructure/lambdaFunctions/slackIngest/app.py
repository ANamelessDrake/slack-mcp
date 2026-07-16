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

MESSAGE_TTL_SECONDS = 30 * 24 * 3600
SIGNATURE_WINDOW_SECONDS = 300

_TABLE = None


def _table():
    global _TABLE
    if _TABLE is None:
        _TABLE = boto3.resource("dynamodb").Table(os.environ["MESSAGES_TABLE"])
    return _TABLE


@lru_cache(maxsize=1)
def _signing_secret() -> str:
    direct = os.environ.get("SLACK_SIGNING_SECRET")
    if direct:
        return direct
    client = boto3.client("secretsmanager")
    return client.get_secret_value(SecretId=os.environ["SIGNING_SECRET_NAME"])["SecretString"]


def _valid_signature(headers: dict, body: str) -> bool:
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
    expected = "v0=" + hmac.new(_signing_secret().encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


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

    item = {
        "PK": f"CH#{channel}",
        "SK": f"TS#{ts}",
        "channel": channel,
        "ts": ts,
        "thread_ts": ev.get("thread_ts", ""),
        "text": text,
        "user": ev.get("user") or ev.get("bot_id") or "",
        "sender_type": "agent" if agent_id else ("bot" if ev.get("bot_id") else "human"),
        "agent_id": agent_id,
        "mentions": re.findall(r"<@([A-Z0-9]+)>", text),
        "slack_event_id": payload.get("event_id", ""),
        "ttl": int(time.time()) + MESSAGE_TTL_SECONDS,
    }

    # Dedupe on (channel, ts): idempotent against Slack's delivery retries and,
    # later, against multiple subscribing apps (DESIGN.md section 3).
    try:
        _table().put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
        log.info("stored message %s %s (sender=%s)", channel, ts, item["sender_type"])
    except _table().meta.client.exceptions.ConditionalCheckFailedException:
        log.info("duplicate message %s %s ignored", channel, ts)
        return _response(200, "ok")

    try:
        _publish({k: v for k, v in item.items() if k not in ("PK", "SK", "ttl")})
    except Exception:
        log.warning("events publish failed for %s %s", channel, ts, exc_info=True)

    return _response(200, "ok")

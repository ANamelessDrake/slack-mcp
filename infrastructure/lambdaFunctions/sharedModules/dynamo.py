"""Inbox table access: message queries and per-identity read cursors.

Cursors are per (identity, channel) so multiple consumers can each track their
own read position over the same messages (DESIGN.md section 5); there is no
shared "delivered" flag.
"""

import os
from functools import lru_cache

import boto3
from boto3.dynamodb.conditions import Key


@lru_cache(maxsize=1)
def messages_table():
    return boto3.resource("dynamodb").Table(os.environ["MESSAGES_TABLE"])


def get_cursor(identity: str, channel: str) -> str:
    resp = messages_table().get_item(Key={"PK": f"CURSOR#{identity}", "SK": f"CH#{channel}"})
    return resp.get("Item", {}).get("last_ts", "0")


def set_cursor(identity: str, channel: str, last_ts: str) -> None:
    messages_table().put_item(
        Item={"PK": f"CURSOR#{identity}", "SK": f"CH#{channel}", "last_ts": last_ts}
    )


def heartbeat_session(identity: str, wait_seconds: int) -> None:
    """Record that this identity is online (a wait_for_messages call is active).

    The TTL outlives the wait by a grace period so back-to-back waits read as one
    continuous session (DESIGN.md section 6).
    """
    import time

    messages_table().put_item(
        Item={
            "PK": f"SESSION#{identity}",
            "SK": "META",
            "identity": identity,
            "ttl": int(time.time()) + wait_seconds + 300,
        }
    )


USER_CACHE_TTL_SECONDS = 24 * 3600


def get_cached_user(user_id: str) -> dict | None:
    """The USER# name-cache item ingest fills, if it is still live."""
    resp = messages_table().get_item(Key={"PK": f"USER#{user_id}", "SK": "META"})
    return resp.get("Item")


def cache_user(user_id: str, name: str, is_bot: bool = False) -> None:
    """Same cache ingest writes, plus is_bot. Items written by ingest carry no
    is_bot, so readers that need it refetch once and enrich the row."""
    import time

    messages_table().put_item(
        Item={
            "PK": f"USER#{user_id}",
            "SK": "META",
            "name": name,
            "is_bot": is_bot,
            "ttl": int(time.time()) + USER_CACHE_TTL_SECONDS,
        }
    )


def channel_known(channel: str) -> bool:
    resp = messages_table().get_item(Key={"PK": "CHANNELS", "SK": f"CH#{channel}"})
    return "Item" in resp


def register_channel(channel: str, channel_type: str = "") -> None:
    messages_table().put_item(
        Item={
            "PK": "CHANNELS",
            "SK": f"CH#{channel}",
            "channel": channel,
            "channel_type": channel_type,
        }
    )


def list_known_channels() -> list[str]:
    """Every conversation the ingest has seen a message in, including agent DMs
    that Slack's channel-listing APIs cannot enumerate."""
    resp = messages_table().query(KeyConditionExpression=Key("PK").eq("CHANNELS"))
    return [item["SK"].removeprefix("CH#") for item in resp.get("Items", [])]


def recent_messages(channel: str, limit: int = 20) -> list[dict]:
    """The last `limit` stored messages in a conversation, oldest first.
    Does not touch read cursors: history reads are non-consuming."""
    resp = messages_table().query(
        KeyConditionExpression=Key("PK").eq(f"CH#{channel}")
        & Key("SK").begins_with("TS#"),
        ScanIndexForward=False,
        Limit=limit,
    )
    return list(reversed(resp.get("Items", [])))


def messages_after(channel: str, after_ts: str, limit: int = 20) -> list[dict]:
    resp = messages_table().query(
        KeyConditionExpression=Key("PK").eq(f"CH#{channel}") & Key("SK").gt(f"TS#{after_ts}"),
        Limit=limit,
    )
    return resp.get("Items", [])

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


def messages_after(channel: str, after_ts: str, limit: int = 20) -> list[dict]:
    resp = messages_table().query(
        KeyConditionExpression=Key("PK").eq(f"CH#{channel}") & Key("SK").gt(f"TS#{after_ts}"),
        Limit=limit,
    )
    return resp.get("Items", [])

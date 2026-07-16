"""Slack file access.

Files posted to Slack live behind auth-gated `url_private` URLs that only a bot
token can fetch, so the server proxies them: clients never see a Slack token.

Two guardrails live here (DESIGN.md section 8):
- Size cap (max_file_download_mb): checked against the stored metadata before
  the fetch and again against the bytes actually read, so a lying or stale
  size cannot blow up the Lambda's memory.
- Known-file rule: only files the ingest recorded (FILE# items) can be
  fetched. The relay can technically see every file in its channels; requiring
  a file to have arrived in an ingested message keeps file access on the same
  per-conversation opt-in as messages, and stops an agent from enumerating
  workspace file IDs.
"""

import os
import urllib.request
from functools import lru_cache

from sharedModules.dynamo import messages_table

TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_EXACT = {
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/javascript",
    "application/x-sh",
    "application/x-python",
    "application/sql",
}


class FileTooLarge(Exception):
    pass


class FileUnknown(Exception):
    pass


def max_bytes() -> int:
    return int(float(os.environ.get("MAX_FILE_DOWNLOAD_MB", "10")) * 1024 * 1024)


def is_text(mimetype: str) -> bool:
    return mimetype.startswith(TEXT_MIME_PREFIXES) or mimetype in TEXT_MIME_EXACT


def is_image(mimetype: str) -> bool:
    return mimetype.startswith("image/") and not mimetype.endswith("svg+xml")


@lru_cache(maxsize=1)
def _relay_token() -> str:
    direct = os.environ.get("RELAY_BOT_TOKEN")
    if direct:
        return direct
    import boto3

    client = boto3.client("secretsmanager")
    name = os.environ["RELAY_BOT_TOKEN_SECRET"]
    return client.get_secret_value(SecretId=name)["SecretString"]


def get_file_record(file_id: str) -> dict:
    """Metadata for a file the ingest recorded. Raises FileUnknown otherwise."""
    resp = messages_table().get_item(Key={"PK": f"FILE#{file_id}", "SK": "META"})
    item = resp.get("Item")
    if not item:
        raise FileUnknown(
            f"file '{file_id}' is not in this system's message history; only files "
            "posted to conversations the relay is in can be read"
        )
    return item


def fetch_bytes(record: dict) -> bytes:
    """Download a file's bytes with the relay bot token, enforcing the size cap."""
    limit = max_bytes()
    declared = int(record.get("size", 0) or 0)
    if declared > limit:
        raise FileTooLarge(
            f"file is {declared / 1048576:.1f} MB, over the {limit / 1048576:.0f} MB limit"
        )

    req = urllib.request.Request(
        record["url_private"],
        headers={"Authorization": f"Bearer {_relay_token()}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        # Read one byte past the cap so an understated size is still caught
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise FileTooLarge(f"file exceeds the {limit / 1048576:.0f} MB limit")
    return data

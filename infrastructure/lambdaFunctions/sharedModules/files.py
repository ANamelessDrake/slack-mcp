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

import json
import os
import re
import urllib.parse
import urllib.request
from functools import lru_cache

from sharedModules.dynamo import list_known_channels, messages_table

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")

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


class FileAccessDenied(Exception):
    pass


def max_bytes() -> int:
    return int(float(os.environ.get("MAX_FILE_DOWNLOAD_MB", "10")) * 1024 * 1024)


def is_text(mimetype: str) -> bool:
    return mimetype.startswith(TEXT_MIME_PREFIXES) or mimetype in TEXT_MIME_EXACT


def is_image(mimetype: str) -> bool:
    return mimetype.startswith("image/") and not mimetype.endswith("svg+xml")


def safe_filename(name: str, fallback: str) -> str:
    """A filename safe to place in a shell command or an HTTP header.

    Slack filenames are attacker-controlled: whoever uploads a file to a watched
    conversation chooses the name, so it can carry shell metacharacters, path
    traversal, quotes, or CRLF. Collapse to a conservative charset, drop leading
    dots (hidden files and `..`), and cap the length. Non-ASCII names degrade to
    underscores, which is the intended trade for inertness.
    """
    cleaned = _UNSAFE_FILENAME.sub("_", name or "").lstrip(".")[:64]
    return cleaned or fallback


@lru_cache(maxsize=1)
def _relay_token() -> str:
    direct = os.environ.get("RELAY_BOT_TOKEN")
    if direct:
        return direct
    from sharedModules.slack import relay_token

    return relay_token()


def _agent_token() -> str:
    direct = os.environ.get("AGENT_BOT_TOKEN")
    if direct:
        return direct
    from sharedModules.identity import current_agent_id
    from sharedModules.slack import agent_token

    return agent_token(current_agent_id())


def _token_for(record: dict) -> str:
    """Which app's token can actually see this file.

    A file posted in an agent's DM is invisible to the relay: the relay is not
    in that conversation, and Slack answers with its login page rather than an
    error. The agent whose DM it is can see it, so DMs use the calling agent's
    token and everything else uses the relay's.
    """
    if str(record.get("channel", "")).startswith("D"):
        return _agent_token()
    return _relay_token()


def _slack_files_info(file_id: str, token: str) -> dict:
    req = urllib.request.Request(
        "https://slack.com/api/files.info?file=" + urllib.parse.quote(file_id, safe=""),
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def get_file_record(file_id: str) -> dict:
    """Metadata for a readable file. Raises FileUnknown if it is out of bounds.

    Ingest records every attachment it sees, but files posted before this
    system existed (or before file support shipped) have no record. Rather than
    leaving the whole back catalogue unreadable, fall back to Slack's files.info
    and enforce the same boundary live: the file must be shared into a
    conversation this system has actually seen messages from. That keeps file
    access on the per-conversation opt-in and still refuses arbitrary file IDs.
    """
    resp = messages_table().get_item(Key={"PK": f"FILE#{file_id}", "SK": "META"})
    item = resp.get("Item")
    if item:
        return item

    # Which app can see it is unknown before the lookup, so try the relay (channels)
    # and then the calling agent (its own DMs).
    data = {}
    for token in (_relay_token(), _agent_token()):
        data = _slack_files_info(file_id, token)
        if data.get("ok"):
            break
    if not data.get("ok"):
        raise FileUnknown(
            f"file '{file_id}' is not readable: Slack said '{data.get('error')}'"
        )

    info = data.get("file") or {}
    shared_in = set(
        (info.get("channels") or []) + (info.get("groups") or []) + (info.get("ims") or [])
    )
    known = set(list_known_channels())
    if not shared_in & known:
        raise FileUnknown(
            f"file '{file_id}' is not shared into any conversation this system has "
            "seen; only files posted where the relay is present can be read"
        )

    return {
        "file_id": file_id,
        "name": info.get("name", ""),
        "mimetype": info.get("mimetype", ""),
        "size": int(info.get("size", 0) or 0),
        "url_private": info["url_private"],
        "channel": next(iter(shared_in & known)),
        "ts": str(info.get("timestamp", "")),
    }


def _reject_login_page(record: dict, content_type: str, data: bytes) -> None:
    """Slack serves its login page, with HTTP 200, when the fetching token
    cannot see a file. Without this check an auth failure reads as a successful
    download of ~61 KB of HTML, and callers trusting ok=true would consume
    Slack's sign-in page as file content."""
    expected = str(record.get("mimetype", "")).lower()
    looks_like_login = b"isLoggedOutRedirect" in data[:8192]
    html_response = content_type.lower().startswith("text/html")
    if not looks_like_login and not (html_response and not expected.startswith("text/html")):
        return
    raise FileAccessDenied(
        f"Slack returned its login page instead of '{record.get('name', '')}': the app "
        "fetching this file cannot see it. Files in an agent's DM need files:read on "
        "that agent's Slack app; files in a channel need the relay to be a member."
    )


def fetch_bytes(record: dict) -> bytes:
    """Download a file's bytes with whichever app's token can see it."""
    limit = max_bytes()
    declared = int(record.get("size", 0) or 0)
    if declared > limit:
        raise FileTooLarge(
            f"file is {declared / 1048576:.1f} MB, over the {limit / 1048576:.0f} MB limit"
        )

    req = urllib.request.Request(
        record["url_private"],
        headers={"Authorization": f"Bearer {_token_for(record)}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_type = resp.headers.get("Content-Type", "")
        # Read one byte past the cap so an understated size is still caught
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise FileTooLarge(f"file exceeds the {limit / 1048576:.0f} MB limit")
    _reject_login_page(record, content_type, data)
    return data

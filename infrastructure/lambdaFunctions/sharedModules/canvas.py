"""Slack canvas access.

A canvas is a collaborative document attached to a channel (one per channel on
free workspaces). This module creates, reads, and edits them section by section
so an agent can maintain a living document without clobbering human edits
(DESIGN.md section on canvases).

Reading: a canvas is a `quip` file whose content comes back as HTML from
url_private, with each block carrying an `id` attribute. Those ids are the same
section ids canvases.edit targets, so parsing the HTML gives both a readable
markdown view and the handles needed to edit any single block in place.

Identity: writes use the calling agent's token (canvases show that agent as
author, like messages); reads use the relay for channels and the agent for its
own DMs, matching the file-access rules.
"""

import urllib.request
from html.parser import HTMLParser

from sharedModules.identity import current_agent_id
from sharedModules.slack import agent_client, agent_token, relay_client, relay_token

# Top-level canvas blocks that carry a targetable id
_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote"}
_HEADING_LEVEL = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}


class CanvasError(Exception):
    pass


def _is_dm(channel: str) -> bool:
    return channel.startswith("D")


def _write_client():
    return agent_client(current_agent_id())


def _read_client(channel: str):
    return agent_client(current_agent_id()) if _is_dm(channel) else relay_client()


def _read_token(channel: str) -> str:
    return agent_token(current_agent_id()) if _is_dm(channel) else relay_token()


def channel_canvas_id(channel: str) -> str | None:
    """The file id of a channel's canvas tab, or None if it has no canvas."""
    resp = relay_client().conversations_info(channel=channel)
    tabz = ((resp.get("channel") or {}).get("properties") or {}).get("tabz") or []
    for tab in tabz:
        if tab.get("type") == "canvas":
            return (tab.get("data") or {}).get("file_id")
    return None


def create_channel_canvas(channel: str, markdown: str) -> str:
    resp = _write_client().api_call(
        "conversations.canvases.create",
        json={
            "channel_id": channel,
            "document_content": {"type": "markdown", "markdown": markdown},
        },
    )
    return resp["canvas_id"]


def apply_changes(canvas_id: str, changes: list[dict]) -> None:
    _write_client().api_call(
        "canvases.edit", json={"canvas_id": canvas_id, "changes": changes}
    )


class _CanvasParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks: list[dict] = []
        self._cur: dict | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self._cur = {"id": dict(attrs).get("id", ""), "type": tag}
            self._text = []

    def handle_data(self, data):
        if self._cur is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS and self._cur is not None:
            self._cur["text"] = "".join(self._text).strip()
            self.blocks.append(self._cur)
            self._cur = None
            self._text = []


def _block_markdown(block: dict) -> str:
    tag, text = block["type"], block["text"]
    if tag in _HEADING_LEVEL:
        return f"{_HEADING_LEVEL[tag]} {text}"
    if tag == "li":
        return f"- {text}"
    if tag == "blockquote":
        return f"> {text}"
    return text


def parse_canvas(html: str) -> dict:
    """HTML canvas content -> {sections: [{section_id, type, text}], markdown}.

    Only blocks that carry an id are returned as sections, since those are the
    ones canvases.edit can target."""
    parser = _CanvasParser()
    parser.feed(html)
    sections = [
        {"section_id": b["id"], "type": b["type"], "text": b["text"]}
        for b in parser.blocks
        if b["id"]
    ]
    markdown = "\n\n".join(
        _block_markdown(b) for b in parser.blocks if b["text"] or b["id"]
    )
    return {"sections": sections, "markdown": markdown}


def fetch_canvas(channel: str, canvas_id: str) -> dict:
    """Read a canvas's current content as parsed sections plus markdown."""
    info = _read_client(channel).files_info(file=canvas_id)
    url = (info.get("file") or {}).get("url_private")
    if not url:
        raise CanvasError(f"canvas '{canvas_id}' has no readable content")

    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {_read_token(channel)}"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    # Same login-page failure mode as files: Slack serves its sign-in page with
    # HTTP 200 when the reading app cannot see the conversation.
    if "isLoggedOutRedirect" in html[:8192]:
        raise CanvasError(
            "cannot read this canvas: the reading app is not in this conversation"
        )
    return parse_canvas(html)


def find_section_id(channel: str, canvas_id: str, find_text: str) -> str:
    """First section whose text contains find_text (case-insensitive), or ''."""
    needle = find_text.strip().lower()
    for section in fetch_canvas(channel, canvas_id)["sections"]:
        if needle in section["text"].lower():
            return section["section_id"]
    return ""

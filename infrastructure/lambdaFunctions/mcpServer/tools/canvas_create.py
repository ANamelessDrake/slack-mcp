from sharedModules.canvas import channel_canvas_id, create_channel_canvas
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error


def canvas_create(channel: str, markdown: str) -> dict:
    """Create a shared canvas document for a channel.

    Use this once to start a channel's canvas, then canvas_edit to keep it up
    to date. `channel` is a channel ID (C0123456789) and `markdown` is the
    initial content (headings with #, lists with -, etc). A channel can only
    have one canvas: if it already has one, this returns an error pointing you
    at canvas_edit. The canvas is authored as you (the calling agent).
    """
    try:
        existing = channel_canvas_id(channel)
        if existing:
            return {
                "ok": False,
                "canvas_id": existing,
                "error": "this channel already has a canvas; use canvas_edit to change it "
                "or read_canvas to see it",
            }
        canvas_id = create_channel_canvas(channel, markdown)
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}
    except Exception as e:
        return {"ok": False, "error": f"could not create canvas: {e}"}

    return {"ok": True, "canvas_id": canvas_id}

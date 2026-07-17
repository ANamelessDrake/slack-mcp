from sharedModules.canvas import channel_canvas_id, set_canvas_title
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error


def canvas_set_title(channel: str, title: str) -> dict:
    """Set or change the title of a channel's canvas.

    `channel` is a channel ID (C0123456789) and `title` is the new name shown
    on the canvas tab. The channel must already have a canvas (create one with
    canvas_create). This changes only the tab title, not the document content.
    """
    if not title.strip():
        return {"ok": False, "error": "title must not be empty"}
    try:
        canvas_id = channel_canvas_id(channel)
        if not canvas_id:
            return {
                "ok": False,
                "error": "this channel has no canvas yet; create one with canvas_create",
            }
        set_canvas_title(canvas_id, title)
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}
    except Exception as e:
        return {"ok": False, "error": f"could not set canvas title: {e}"}

    return {"ok": True, "canvas_id": canvas_id, "title": title}

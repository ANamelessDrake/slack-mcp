from sharedModules.canvas import CanvasError, channel_canvas_id, fetch_canvas


def read_canvas(channel: str) -> dict:
    """Read a channel's canvas (its shared document).

    `channel` is a channel ID (C0123456789). Returns the canvas as `markdown`
    plus a list of `sections`, each with a `section_id`, `type` (h1, h2,
    paragraph, li), and `text`. Pass a section's `section_id` (or matching text
    via `find_text`) to canvas_edit to change just that part in place. Returns
    an error if the channel has no canvas yet; create one with canvas_create.
    """
    try:
        canvas_id = channel_canvas_id(channel)
        if not canvas_id:
            return {
                "ok": False,
                "error": "this channel has no canvas yet; create one with canvas_create",
            }
        data = fetch_canvas(channel, canvas_id)
    except CanvasError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"could not read canvas: {e}"}

    return {
        "ok": True,
        "canvas_id": canvas_id,
        "sections": data["sections"],
        "markdown": data["markdown"],
    }

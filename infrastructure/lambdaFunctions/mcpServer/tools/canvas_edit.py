from sharedModules.canvas import (
    CanvasError,
    apply_changes,
    channel_canvas_id,
    find_section_id,
)
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error

# tool operation -> Slack canvases.edit operation
_POSITIONAL = {
    "replace": "replace",
    "insert_after": "insert_after",
    "insert_before": "insert_before",
    "delete": "delete",
}
_WHOLE = {"append": "insert_at_end", "prepend": "insert_at_start"}


def canvas_edit(
    channel: str,
    operation: str,
    markdown: str = "",
    find_text: str = "",
    section_id: str = "",
) -> dict:
    """Change a channel's canvas in place, one section at a time.

    `operation` is one of:
      - append / prepend: add `markdown` at the end or start (no target needed)
      - replace: swap one section's content for `markdown`
      - insert_after / insert_before: add `markdown` next to a section
      - delete: remove a section

    For replace, insert_after, insert_before, and delete, name the target
    section either by `section_id` (from read_canvas) or by `find_text` (any
    text the section contains). Editing one section leaves the rest, including
    anything a human wrote, untouched. For replace, give `markdown` for that one
    block (for example a single heading or line) to keep the layout clean. Read
    the canvas first with read_canvas if you are unsure what is there.
    """
    if operation not in _WHOLE and operation not in _POSITIONAL:
        valid = sorted(list(_WHOLE) + list(_POSITIONAL))
        return {"ok": False, "error": f"operation must be one of {valid}"}

    try:
        canvas_id = channel_canvas_id(channel)
        if not canvas_id:
            return {
                "ok": False,
                "error": "this channel has no canvas yet; create one with canvas_create",
            }

        doc = {"type": "markdown", "markdown": markdown}
        if operation in _WHOLE:
            change = {"operation": _WHOLE[operation], "document_content": doc}
        else:
            target = section_id or (
                find_section_id(channel, canvas_id, find_text) if find_text else ""
            )
            if not target:
                return {
                    "ok": False,
                    "error": f"'{operation}' needs a target: pass section_id or find_text. "
                    "Use read_canvas to see the sections.",
                }
            change = {"operation": _POSITIONAL[operation], "section_id": target}
            if operation != "delete":
                change["document_content"] = doc

        apply_changes(canvas_id, [change])
    except CanvasError as e:
        return {"ok": False, "error": str(e)}
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}
    except Exception as e:
        return {"ok": False, "error": f"could not edit canvas: {e}"}

    return {"ok": True, "canvas_id": canvas_id, "operation": operation}

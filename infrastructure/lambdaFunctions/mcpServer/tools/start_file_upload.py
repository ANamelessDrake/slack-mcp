import os

from sharedModules.identity import current_agent_id
from sharedModules.slack import agent_client
from slack_sdk.errors import SlackApiError

from .send_message import _slack_error


def start_file_upload(file_name: str, size_bytes: int) -> dict:
    """Begin uploading a file too large to pass inline to send_message.

    send_message's `file_base64` is capped around 4 MB, because those bytes ride
    inside the request. For anything larger, use this: it returns an `upload_url`
    and a `file_id`. POST the raw file bytes to `upload_url` yourself (a plain
    HTTP POST with the file as the body), which goes straight to Slack with no
    size cap and never through this server. Then call send_message with
    `uploaded_file_id` set to the `file_id`, and the file posts into the channel
    or DM with your `text` as its comment.

    `size_bytes` is the file's exact byte length, which Slack uses to provision
    the upload. Pass the real length; a mismatch makes the upload fail.
    """
    name = file_name.strip()
    if not name:
        return {"ok": False, "error": "file_name is required"}
    if size_bytes <= 0:
        return {"ok": False, "error": "size_bytes must be a positive byte count"}

    try:
        resp = agent_client(current_agent_id()).files_getUploadURLExternal(
            filename=os.path.basename(name) or name, length=size_bytes
        )
    except SlackApiError as e:
        return {"ok": False, "error": _slack_error(e)}

    return {
        "ok": True,
        "upload_url": resp["upload_url"],
        "file_id": resp["file_id"],
        "next_step": (
            "POST the raw file bytes to upload_url, then call send_message with "
            "uploaded_file_id set to this file_id to post it into a conversation."
        ),
    }

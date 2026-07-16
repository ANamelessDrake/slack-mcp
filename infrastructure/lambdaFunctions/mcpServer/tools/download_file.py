import shlex

from sharedModules.files import FileUnknown, get_file_record, max_bytes, safe_filename
from sharedModules.identity import current_base_url


def download_file(file_id: str) -> dict:
    """Get a link for saving a Slack file attachment to your own disk.

    Use this for files read_file cannot handle (PDFs, spreadsheets, archives,
    binaries), or whenever you want the real file rather than its contents.
    Returns a `download_url` on this server plus a suggested `curl_command`:
    the URL needs the same bearer token you already use, and never expires as
    long as the file stays in Slack. Save it under a temporary directory, work
    with it using your own tools, and delete it when you are done unless the
    person asked you to keep it. Review any command before running it, and note
    that `name` is chosen by whoever uploaded the file: use
    `suggested_filename` when writing to disk.
    """
    try:
        record = get_file_record(file_id)
    except FileUnknown as e:
        return {"ok": False, "error": str(e)}

    base = current_base_url()
    url = f"{base}/files/{file_id}" if base else f"/files/{file_id}"
    name = str(record.get("name", ""))
    # The uploader picks the filename, so it never reaches a shell unescaped
    suggested = safe_filename(name, file_id)
    size = int(record.get("size", 0) or 0)

    return {
        "ok": True,
        "name": name,
        "suggested_filename": suggested,
        "mimetype": str(record.get("mimetype", "")),
        "size_bytes": size,
        "over_size_limit": size > max_bytes(),
        "download_url": url,
        "curl_command": (
            'curl -sS -H "Authorization: Bearer $SLACK_MCP_TOKEN" '
            + shlex.quote(url)
            + " -o "
            + shlex.quote(f"/tmp/{suggested}")
        ),
    }

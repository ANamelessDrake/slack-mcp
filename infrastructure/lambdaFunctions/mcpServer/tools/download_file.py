from sharedModules.files import FileUnknown, get_file_record, max_bytes
from sharedModules.identity import current_base_url


def download_file(file_id: str) -> dict:
    """Get a link for saving a Slack file attachment to your own disk.

    Use this for files read_file cannot handle (PDFs, spreadsheets, archives,
    binaries), or whenever you want the real file rather than its contents.
    Returns a `download_url` on this server plus a ready-to-run `curl_command`:
    the URL needs the same bearer token you already use, and never expires as
    long as the file stays in Slack. Save it under a temporary directory, work
    with it using your own tools, and delete it when you are done unless the
    person asked you to keep it.
    """
    try:
        record = get_file_record(file_id)
    except FileUnknown as e:
        return {"ok": False, "error": str(e)}

    base = current_base_url()
    url = f"{base}/files/{file_id}" if base else f"/files/{file_id}"
    name = str(record.get("name", file_id))
    size = int(record.get("size", 0) or 0)

    return {
        "ok": True,
        "name": name,
        "mimetype": str(record.get("mimetype", "")),
        "size_bytes": size,
        "over_size_limit": size > max_bytes(),
        "download_url": url,
        "curl_command": (
            f'curl -sS -H "Authorization: Bearer $SLACK_MCP_TOKEN" '
            f'"{url}" -o "/tmp/{name}"'
        ),
    }

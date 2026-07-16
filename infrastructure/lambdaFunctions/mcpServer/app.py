"""slack-mcp MCP server.

FastMCP application served over streamable HTTP. On Lambda it runs behind Lambda
Web Adapter (uvicorn started by run.sh); locally:

    DEV_BEARER_TOKEN=local-token python -m uvicorn app:app --port 8000

The server is stateless (no MCP session affinity), which is required on Lambda
where consecutive requests may hit different containers.
"""

import logging

from auth.bearer import BearerAuthMiddleware
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sharedModules.files import (
    FileTooLarge,
    FileUnknown,
    fetch_bytes,
    get_file_record,
    is_image,
    safe_filename,
)
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from tools import register_all

logging.basicConfig(level=logging.INFO)

# DNS rebinding protection guards localhost-bound servers; this one is a public
# endpoint on a Function URL domain (unknown at build time) behind bearer auth,
# so Host validation would only 421 legitimate traffic.
mcp = FastMCP(
    "slack-mcp",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
register_all(mcp)

app = mcp.streamable_http_app()
app.add_middleware(BearerAuthMiddleware)


async def health(_request):
    return PlainTextResponse("ok")


async def serve_file(request):
    """Proxy a Slack attachment's bytes to a client that already holds a bearer
    token, so clients can save real files without ever seeing a Slack token.
    Guarded by the same middleware as every other route."""
    file_id = request.path_params["file_id"]
    try:
        record = get_file_record(file_id)
        data = fetch_bytes(record)
    except FileUnknown as e:
        return PlainTextResponse(str(e), status_code=404)
    except FileTooLarge as e:
        return PlainTextResponse(str(e), status_code=413)

    # The uploader controls both the filename and the reported mimetype, so
    # neither is echoed back raw: a name could inject headers, and serving an
    # uploaded text/html from this origin would make it executable in a browser.
    name = safe_filename(str(record.get("name", "")), file_id)
    mimetype = str(record.get("mimetype") or "")
    return Response(
        content=data,
        media_type=mimetype if is_image(mimetype) else "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


async def no_get_stream(_request):
    # Stateless server: we never send server-initiated messages, so we refuse
    # the standing GET listener stream (the MCP spec allows 405 for this).
    # Without it, every connected client parks a Lambda invocation that runs
    # until the function timeout kills it, stalling clients and burning money.
    return PlainTextResponse("Method Not Allowed", status_code=405, headers={"Allow": "POST"})


app.router.routes.insert(0, Route("/mcp", no_get_stream, methods=["GET"]))
app.router.routes.append(Route("/files/{file_id}", serve_file, methods=["GET"]))
app.router.routes.append(Route("/health", health, methods=["GET"]))

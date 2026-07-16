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
from starlette.responses import PlainTextResponse
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


async def no_get_stream(_request):
    # Stateless server: we never send server-initiated messages, so we refuse
    # the standing GET listener stream (the MCP spec allows 405 for this).
    # Without it, every connected client parks a Lambda invocation that runs
    # until the function timeout kills it, stalling clients and burning money.
    return PlainTextResponse("Method Not Allowed", status_code=405, headers={"Allow": "POST"})


app.router.routes.insert(0, Route("/mcp", no_get_stream, methods=["GET"]))
app.router.routes.append(Route("/health", health, methods=["GET"]))

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


app.router.routes.append(Route("/health", health, methods=["GET"]))

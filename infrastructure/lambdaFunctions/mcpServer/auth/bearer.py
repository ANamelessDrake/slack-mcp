"""Deployment-local bearer token auth with a token-per-agent map.

Single-tenant by design (DESIGN.md section 4.1): tokens are static and live in
Secrets Manager. Each agent has its own token ({Env}-{Project}-McpToken-{id}),
and the original DevBearerToken remains valid, mapped to DEFAULT_AGENT_ID. The
matched agent identity is stored in a request-scoped ContextVar for the tools.

For local development and tests, DEV_BEARER_TOKEN short-circuits Secrets
Manager and maps to DEFAULT_AGENT_ID.
"""

import hmac
import logging
import os
from functools import lru_cache

from sharedModules.identity import set_current_agent
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger(__name__)

PUBLIC_PATHS = {"/health"}


@lru_cache(maxsize=1)
def token_map() -> dict[str, str]:
    """Map of bearer token value to agent_id."""
    default_agent = os.environ.get("DEFAULT_AGENT_ID", "")

    direct = os.environ.get("DEV_BEARER_TOKEN")
    if direct:
        return {direct: default_agent}

    import boto3

    client = boto3.client("secretsmanager")
    mapping: dict[str, str] = {}

    legacy = client.get_secret_value(SecretId=os.environ["DEV_BEARER_TOKEN_SECRET"])
    mapping[legacy["SecretString"]] = default_agent

    prefix = os.environ.get("MCP_TOKEN_SECRET_PREFIX", "")
    for agent_id in filter(None, os.environ.get("AGENT_IDS", "").split(",")):
        try:
            secret = client.get_secret_value(SecretId=f"{prefix}{agent_id}")
            mapping[secret["SecretString"]] = agent_id
        except Exception:
            log.warning("no MCP token secret for agent %s", agent_id, exc_info=True)
    return mapping


def _resolve(token: str) -> str | None:
    """Constant-time comparison against every known token."""
    matched = None
    for known, agent_id in token_map().items():
        if hmac.compare_digest(token, known):
            matched = agent_id
    return matched


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        agent_id = _resolve(token) if token else None
        if agent_id is None:
            return JSONResponse(
                {"error": "invalid_token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        set_current_agent(agent_id)
        return await call_next(request)

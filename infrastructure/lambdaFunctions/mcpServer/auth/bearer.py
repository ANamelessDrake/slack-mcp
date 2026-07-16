"""Deployment-local bearer token auth (DESIGN.md section 4.1).

Single-tenant by design: one static token, generated at deploy time, grants
full access. The expected token comes from Secrets Manager
(DEV_BEARER_TOKEN_SECRET); for local development and tests, DEV_BEARER_TOKEN
overrides it directly. The agent-to-agent milestone extends this to a
token-per-agent map.
"""

import hmac
import os
from functools import lru_cache

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

PUBLIC_PATHS = {"/health"}


@lru_cache(maxsize=1)
def expected_token() -> str:
    direct = os.environ.get("DEV_BEARER_TOKEN")
    if direct:
        return direct

    import boto3

    client = boto3.client("secretsmanager")
    secret_name = os.environ["DEV_BEARER_TOKEN_SECRET"]
    return client.get_secret_value(SecretId=secret_name)["SecretString"]


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        if not token or not hmac.compare_digest(token, expected_token()):
            return JSONResponse(
                {"error": "invalid_token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

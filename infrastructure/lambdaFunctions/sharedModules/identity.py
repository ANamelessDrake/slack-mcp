"""Request-scoped agent identity.

The bearer middleware resolves the presented token to an agent_id and stores it
in a ContextVar; tools read it via current_agent_id(). Contexts propagate into
the tasks Starlette spawns per request, so concurrent requests with different
tokens never see each other's identity. Falls back to DEFAULT_AGENT_ID for
local runs and tests that bypass the middleware.
"""

import os
from contextvars import ContextVar

_current_agent: ContextVar[str] = ContextVar("slack_mcp_agent_identity", default="")
_base_url: ContextVar[str] = ContextVar("slack_mcp_base_url", default="")


def set_current_agent(agent_id: str) -> None:
    _current_agent.set(agent_id)


def current_agent_id() -> str:
    return _current_agent.get() or os.environ.get("DEFAULT_AGENT_ID", "")


def set_base_url(url: str) -> None:
    _base_url.set(url.rstrip("/"))


def current_base_url() -> str:
    """This deployment's public origin, taken from the live request.

    Read from the request rather than an env var: the Function URL is created
    by the same stack as the function, so feeding its own URL back in as
    configuration would be a circular CloudFormation dependency.
    """
    return _base_url.get()

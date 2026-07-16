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


def set_current_agent(agent_id: str) -> None:
    _current_agent.set(agent_id)


def current_agent_id() -> str:
    return _current_agent.get() or os.environ.get("DEFAULT_AGENT_ID", "")

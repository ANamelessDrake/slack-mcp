import json

import app as app_module
import pytest
from starlette.testclient import TestClient

AUTH = {"Authorization": "Bearer test-token"}
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


@pytest.fixture(scope="module")
def client():
    # One client for the whole module: the streamable HTTP session manager's
    # lifespan can only be started once per FastMCP app instance.
    with TestClient(app_module.app) as c:
        yield c


def test_health_needs_no_auth(client):
    assert client.get("/health").status_code == 200


def test_mcp_rejects_missing_token(client):
    resp = client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert resp.status_code == 401
    assert resp.headers["WWW-Authenticate"] == "Bearer"


def test_mcp_rejects_wrong_token(client):
    resp = client.post(
        "/mcp",
        json=INITIALIZE,
        headers={**MCP_HEADERS, "Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_get_stream_refused_405(client):
    # No standing SSE listener on a stateless server; clients fall back cleanly
    resp = client.get("/mcp", headers={**AUTH, "Accept": "text/event-stream"})
    assert resp.status_code == 405
    assert resp.headers["Allow"] == "POST"


def test_mcp_initialize_with_valid_token(client):
    resp = client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **AUTH})
    assert resp.status_code == 200
    assert "slack-mcp" in resp.text


def test_tools_are_listed(client):
    init = client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, **AUTH})
    session_id = init.headers.get("mcp-session-id", "")
    headers = {**MCP_HEADERS, **AUTH}
    if session_id:
        headers["mcp-session-id"] = session_id
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=headers,
    )
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers=headers,
    )
    assert resp.status_code == 200
    for tool in ("send_message", "read_thread", "list_channels"):
        assert tool in resp.text, f"{tool} missing from tools/list: {json.dumps(resp.text)[:500]}"

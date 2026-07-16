"""Slack MCP bridge plugin for WilmaV5.

Connects to a slack-mcp deployment (or any streamable HTTP MCP server that
accepts static bearer auth) and registers every tool the server advertises as
a native WILMA tool, so local models can send and receive Slack messages.

Configuration (environment variables):
    SLACK_MCP_URL    the MCP endpoint, e.g. https://xxx.lambda-url.../mcp
    SLACK_MCP_TOKEN  the deployment's bearer token

Install: copy this file into ~/.wilma5_plugins/ (all projects) or a project's
./wilma_plugins/ directory. If the variables are unset the plugin logs a note
and registers nothing.

Implementation note: this speaks JSON-RPC over streamable HTTP directly with
urllib instead of the official mcp SDK. The SDK's client is asyncio-native and
would need its own task lifecycle inside WILMA's loop; the server is stateless,
so two small synchronous POSTs (wrapped in asyncio.to_thread at call time)
cover tools/list and tools/call with zero added dependencies.
"""

import asyncio
import json
import logging
import os
import urllib.request

from wilmaV5.tools import ToolDef

log = logging.getLogger("wilma")

DEFAULT_TIMEOUT_SECONDS = 30
# wait_for_messages blocks server-side up to 110s; give the HTTP call headroom
LONG_POLL_HEADROOM_SECONDS = 20


def _config() -> tuple[str, str]:
    return os.environ.get("SLACK_MCP_URL", ""), os.environ.get("SLACK_MCP_TOKEN", "")


def _rpc(method: str, params: dict, timeout: float) -> dict:
    """One JSON-RPC request over streamable HTTP. Returns the result object."""
    url, token = _config()
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read().decode()

    if "text/event-stream" in content_type:
        message = None
        for line in raw.splitlines():
            if line.startswith("data: "):
                frame = json.loads(line[6:])
                if "result" in frame or "error" in frame:
                    message = frame
        if message is None:
            raise RuntimeError(f"no JSON-RPC response in SSE stream: {raw[:200]}")
    else:
        message = json.loads(raw)

    if "error" in message:
        raise RuntimeError(f"MCP error: {message['error']}")
    return message["result"]


def _call_tool(name: str, params: dict, timeout: float) -> str:
    result = _rpc("tools/call", {"name": name, "arguments": params}, timeout)
    texts = [
        block.get("text", "")
        for block in result.get("content", [])
        if block.get("type") == "text"
    ]
    text = "\n".join(t for t in texts if t) or json.dumps(result)
    if result.get("isError"):
        return f"Error from Slack server: {text}"
    return text


def _make_tool_func(tool_name: str):
    async def tool_func(**params) -> str:
        timeout = DEFAULT_TIMEOUT_SECONDS
        if tool_name == "wait_for_messages":
            timeout = int(params.get("timeout_seconds", 60) or 60) + LONG_POLL_HEADROOM_SECONDS
        # Blocking urllib call runs in a worker thread so a long wait never
        # stalls WILMA's event loop.
        return await asyncio.to_thread(_call_tool, tool_name, dict(params), timeout)

    return tool_func


def register_tools(registry) -> None:
    url, token = _config()
    if not url or not token:
        log.info("slack_mcp plugin: SLACK_MCP_URL / SLACK_MCP_TOKEN not set; skipping")
        return

    try:
        tools = _rpc("tools/list", {}, DEFAULT_TIMEOUT_SECONDS).get("tools", [])
    except Exception as e:
        log.error("slack_mcp plugin: could not reach %s: %s", url, e)
        return

    for tool in tools:
        name = tool["name"]
        registry.register(
            ToolDef(
                name=name,
                description=tool.get("description", ""),
                parameters=tool.get("inputSchema")
                or {"type": "object", "properties": {}},
                func=_make_tool_func(name),
                # Posting to Slack is outward-facing; everything else is a read
                requires_permission=(name == "send_message"),
            )
        )

    # Small models only see BASE_TOOLS plus already-used tools on turn 1
    try:
        from wilmaV5.agent import BASE_TOOLS

        BASE_TOOLS.update(t["name"] for t in tools)
    except Exception:
        pass

    log.info("slack_mcp plugin: registered %d tools from %s", len(tools), url)

"""MCP tool registration.

One module per tool. Descriptions are written for the weakest caller (8B local
models per DESIGN.md section 7): imperative, explicit about when to use the tool,
flat string parameters, defaults for everything optional.
"""

from mcp.server.fastmcp import FastMCP

from . import check_messages, list_channels, read_thread, send_message, wait_for_messages


def register_all(mcp: FastMCP) -> None:
    mcp.tool()(send_message.send_message)
    mcp.tool()(check_messages.check_messages)
    mcp.tool()(wait_for_messages.wait_for_messages)
    mcp.tool()(read_thread.read_thread)
    mcp.tool()(list_channels.list_channels)

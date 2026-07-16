"""MCP tool registration.

One module per tool. Descriptions are written for the weakest caller (8B local
models per DESIGN.md section 7): imperative, explicit about when to use the tool,
flat string parameters, defaults for everything optional.
"""

from mcp.server.fastmcp import FastMCP

from . import (
    check_messages,
    download_file,
    find_user,
    list_channels,
    read_file,
    read_history,
    read_thread,
    send_message,
    wait_for_messages,
)


def register_all(mcp: FastMCP) -> None:
    mcp.tool()(send_message.send_message)
    mcp.tool()(check_messages.check_messages)
    mcp.tool()(wait_for_messages.wait_for_messages)
    mcp.tool()(read_history.read_history)
    mcp.tool()(read_thread.read_thread)
    mcp.tool()(list_channels.list_channels)
    mcp.tool()(find_user.find_user)
    mcp.tool()(read_file.read_file)
    mcp.tool()(download_file.download_file)

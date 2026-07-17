"""MCP tool registration.

One module per tool. Descriptions are written for the weakest caller (8B local
models per DESIGN.md section 7): imperative, explicit about when to use the tool,
flat string parameters, defaults for everything optional.
"""

from mcp.server.fastmcp import FastMCP

from . import (
    canvas_create,
    canvas_edit,
    canvas_set_title,
    check_messages,
    download_file,
    find_channel,
    find_user,
    list_channels,
    list_members,
    read_canvas,
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
    mcp.tool()(list_members.list_members)
    mcp.tool()(find_channel.find_channel)
    mcp.tool()(find_user.find_user)
    mcp.tool()(read_file.read_file)
    mcp.tool()(download_file.download_file)
    mcp.tool()(read_canvas.read_canvas)
    mcp.tool()(canvas_create.canvas_create)
    mcp.tool()(canvas_edit.canvas_edit)
    mcp.tool()(canvas_set_title.canvas_set_title)

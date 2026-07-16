# WILMA bridge plugin

Registers every tool of a slack-mcp deployment as a native
[WILMA](https://github.com/ANamelessDrake/wilma) tool, so local models
(Dolphin 3, Qwen3) can send and receive Slack messages through the same server
as any other MCP client.

## Install

1. Copy `slack_mcp.py` into `~/.wilma5_plugins/` (all projects) or a project's
   `./wilma_plugins/` directory.
2. Export the connection settings, for example in `~/.bashrc`:

   ```bash
   export SLACK_MCP_URL="https://<your-function-url>/mcp"
   export SLACK_MCP_TOKEN="<your deployment's bearer token>"
   ```

3. Start WILMA. The log line `slack_mcp plugin: registered N tools` confirms
   the bridge is up, and the Slack tools appear alongside the built-ins.

`send_message` asks for permission before posting (WILMA can persist an
always-allow); the read tools do not. The plugin also adds the Slack tools to
`wilmaV5.agent.BASE_TOOLS` so compact-context models see them from turn 1
(requires wilmaV5 with plugin-extendable BASE_TOOLS).

## Working with attachments

`read_file` returns images as viewable content and text files as text, so most
attachments need no local copy. For anything else (PDFs, archives), call
`download_file`, save the bytes under a temporary directory with the returned
curl command, work with your own tools, and delete the copy afterwards unless
the person asked you to keep it.

## Notes

- The plugin is generic: point `SLACK_MCP_URL` at any streamable HTTP MCP
  server that accepts a static bearer token.
- `wait_for_messages` runs in a worker thread with extended HTTP timeout, so
  live-session waits do not stall WILMA's event loop.
- If the environment variables are unset, the plugin deactivates itself with a
  log note instead of failing WILMA startup.

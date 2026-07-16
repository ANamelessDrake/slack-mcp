# Connecting Claude Code

1. Deploy the stacks and note the `McpEndpoint` output of the lambda stack.
2. Fetch the dev bearer token:

   ```bash
   export SLACK_MCP_TOKEN=$(aws secretsmanager get-secret-value \
     --secret-id Dev-SlackMcp-DevBearerToken --query SecretString --output text)
   ```

3. Copy `claude-code.mcp.json` into your project as `.mcp.json`, replacing the URL
   with the `McpEndpoint` value. The `${SLACK_MCP_TOKEN}` reference is expanded
   from the environment by Claude Code.
4. Start Claude Code and run `/mcp` to confirm the `slack` server is connected,
   then ask it to list channels and send a message.

claude.ai custom connectors require OAuth, which this single-tenant design deliberately omits; use Claude Code or Claude Desktop.

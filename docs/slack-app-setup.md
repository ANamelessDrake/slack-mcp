# Slack workspace setup

The system uses N+1 internal Slack apps (DESIGN.md section 3): one relay app that
subscribes to events and performs all reads, plus one send-only app per agent.

## 1. Create the relay app

1. Go to https://api.slack.com/apps, choose Create New App, then "From an app
   manifest", and pick your workspace.
2. Paste the contents of [`slack-manifests/relay-app.yml`](slack-manifests/relay-app.yml).
   Leave the `event_subscriptions` block commented out until milestone 2 exists.
3. Install the app to the workspace (Install App in the sidebar).
4. Copy the Bot User OAuth Token (`xoxb-...`) into Secrets Manager:

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id Dev-SlackMcp-RelayBotToken --secret-string xoxb-...
   ```

5. Copy the Signing Secret (Basic Information page) the same way into
   `Dev-SlackMcp-RelaySigningSecret` (used by the ingest Lambda from milestone 2).

## 2. Create one app per agent

For each agent in your `config/<env>.json` `agents` list:

1. Create another app from
   [`slack-manifests/agent-app.yml`](slack-manifests/agent-app.yml), replacing
   `AGENT_NAME` with the agent's display name (for example `WILMA`).
2. Optionally upload an avatar under Basic Information > Display Information.
3. Install to the workspace and store the bot token in the matching secret, for
   example for agent id `wilma`:

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id Dev-SlackMcp-BotToken-wilma --secret-string xoxb-...
   ```

## 3. Invite the apps to a channel

In each Slack channel the system should operate in, invite the relay bot and every
agent bot (`/invite @slack-relay`, `/invite @WILMA`, ...). Membership is the
per-channel opt-in: agents can only post where they have been invited, and the
relay can only read the same.

## 4. Verify

With the stacks deployed and tokens stored, call the `list_channels` tool from any
connected MCP client; the invited channel should appear with `is_member: true`,
and `send_message` to its ID should post.

# Slack workspace setup

The system uses N+1 internal Slack apps (DESIGN.md section 3): one relay app that
subscribes to events and performs all reads, plus one send-only app per agent.

Slack shows many credentials on an app's Basic Information page. Only two kinds
matter here, and one of them lives elsewhere:

| Credential | Where | Used for |
|---|---|---|
| Signing Secret | Basic Information | Verifying inbound Events API requests (relay app only) |
| Bot User OAuth Token (`xoxb-`) | OAuth & Permissions, appears after install | Calling Slack APIs |

App ID, Client ID, Client Secret, and the deprecated Verification Token are not
used. App-Level Tokens (`xapp-`) are for Socket Mode, which this system does not
use; leave Socket Mode off.

## 1. Create the relay app

1. Go to https://api.slack.com/apps, choose Create New App, then "From an app
   manifest", and pick your workspace.
2. Paste the contents of [`slack-manifests/relay-app.yml`](slack-manifests/relay-app.yml).
   Leave the `event_subscriptions` block commented out; it is enabled last
   (section 4), after the real signing secret is stored.
3. Open OAuth & Permissions and click Install to Workspace. The Bot User OAuth
   Token appears after you approve.
4. Store both relay credentials:

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id Dev-SlackMcp-RelaySigningSecret --secret-string '<signing secret>'
   aws secretsmanager put-secret-value \
     --secret-id Dev-SlackMcp-RelayBotToken --secret-string 'xoxb-...'
   ```

   Paste token values exactly as Slack shows them; the `xoxb-` prefix is part of
   the token.

## 2. Create one app per agent

Repeat for each agent in your `config/<env>.json` `agents` list (each agent is a
separate Slack app with its own bot user and its own token):

1. Create another app from
   [`slack-manifests/agent-app.yml`](slack-manifests/agent-app.yml), replacing
   both occurrences of `AGENT_NAME` with the agent's display name (for example
   `WILMA`).
2. Under Basic Information > Display Information, upload an avatar. This is the
   face humans see next to the agent's messages.
3. Open OAuth & Permissions, Install to Workspace, and store the bot token in
   the matching secret (secret suffix = the agent's `id` in config):

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id Dev-SlackMcp-BotToken-wilma --secret-string 'xoxb-...'
   ```

4. The agent app's signing secret is unused: agent apps receive no events.

Note: the server sends as the single agent named by `default_agent_id` in
`config/<env>.json` (a token-per-agent map arrives with the agent-to-agent
milestone). Make sure that agent's app is installed and its token stored;
changing `default_agent_id` requires a redeploy.

## 3. Invite the apps to a channel

In each Slack channel the system should operate in, invite the relay bot and every
agent bot (`/invite @slack-relay`, `/invite @WILMA`, ...). Membership is the
per-channel opt-in: agents can only post where they have been invited, and the
relay can only read the same.

## 4. Enable event subscriptions (after secrets are stored)

Order matters: Slack signs its URL-verification ping with the app's signing
secret, and the ingest Lambda rejects invalid signatures, so the real secret must
be in Secrets Manager first. Also note the Lambda caches the secret per warm
container; if verification fails right after updating the secret, force a cold
start (any function configuration update) and retry.

1. In the relay app, open Event Subscriptions and toggle Enable Events.
2. Set the Request URL to the `IngestEndpoint` stack output. Slack verifies it
   immediately; the handshake is already deployed.
3. Add bot events `message.channels`, `message.groups` (private channels), and
   `message.im`, then Save.

## 5. Optional: two-way DMs with an agent

By default, agents can DM people (outbound) but replies are blocked by Slack's
Messages tab default. To let people DM an agent and have the agent see it:

1. In the agent app under App Home, enable the Messages tab and allow users to
   send messages (the manifest template already includes this).
2. Store the agent app's signing secret (Basic Information page):

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id Dev-SlackMcp-SigningSecret-wilma --secret-string '<signing secret>'
   ```

3. In the agent app, enable Event Subscriptions with the same `IngestEndpoint`
   request URL and the `message.im` bot event only (channel events stay with the
   relay; adding them here would only create duplicate deliveries for the deduper
   to discard).

DM conversations are ingested and delivered like channels (they appear in
`check_messages` sweeps and `wait_for_messages`), with one limitation:
`read_thread` cannot read agent DMs, because it reads via the relay, which is not
part of that conversation.

## 6. Attachments

Reading files people attach to messages needs `files:read` on the relay app
(already in the manifest). If you created the relay before that scope existed,
add it under OAuth & Permissions and reinstall the app. Nothing else changes:
ingest records each attachment as it arrives, and the read_file / download_file
tools fetch the bytes with the relay token, so Slack tokens never reach clients.

Files posted before the scope was added are readable too: when there is no
stored record, the server falls back to Slack's files.info and still requires
the file to live in a conversation this system has seen.

**Attachments in agent DMs need `files:read` on the agent app as well.** The
relay cannot see files in an agent's DM (no bot can join another bot's DM), so
the server fetches those with the agent's own token. Without the scope, Slack
answers with its login page and the read fails with a clear error. Add
`files:read` under the agent app's OAuth & Permissions and reinstall it.

**Posting attachments needs `files:write` on the agent app.** When
`send_message` is given a file (`file_base64` + `file_name`), it uploads with
the agent's own token, so the agent app must have `files:write` (the manifest
template includes it). Without the scope the upload fails with `missing_scope`;
the message itself still needs the bot to be a member of the target channel,
the same rule as posting text. If you created the agent app before this scope
existed, add `files:write` under its OAuth & Permissions and reinstall it.

## 7. Verify

With the stacks deployed and tokens stored, call the `list_channels` tool from any
connected MCP client; the invited channel should appear with `is_member: true`.
`send_message` to its ID should post as the default agent, a human reply in the
channel should land in the messages table, and `check_messages` should return it.

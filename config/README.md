# Environment configuration

Copy `example.json` to `<env>.json` (for example `dev.json`) and fill in the values.
Real config files are gitignored because they carry your AWS account id and email.

| Field | Meaning |
|---|---|
| `account` | AWS account id to deploy into |
| `region` | AWS region (default `us-east-1`) |
| `environment_name` / `environment_name_upper` | Environment label used in resource names (`dev` / `Dev`) |
| `project_name` / `project_name_upper` | Project label used in resource names (`slackmcp` / `SlackMcp`) |
| `alarm_email` | Destination for CloudWatch alarms (used from the monitoring milestone onward) |
| `message_retention_days` | How long stored Slack messages live in DynamoDB (default 30; `0` keeps them forever) |
| `owner_name` | Names the owner in the server's confidentiality policy, which tells agents not to relay the owner's email or private messages to others without permission (optional; generic phrasing if empty) |
| `default_agent_id` | Agent identity bound to the legacy DevBearerToken (per-agent McpToken secrets map to their own identities) |
| `agents` | One entry per agent Slack app; `id` is used in secret names and message attribution |
| `agent_turn_budget` | Max consecutive agent messages in a conversation with no human reply (default 6) |
| `max_file_download_mb` | Largest Slack attachment the server will fetch (default 10) |
| `agent_cooldown_seconds` | Minimum seconds between different agents' messages in one conversation (default 3) |

Deploy with `./deployCDK.sh <env>`, which passes `--context env=<env>` to the CDK app.

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
| `default_agent_id` | Agent identity used by the milestone 1 static-token auth placeholder |
| `agents` | One entry per agent Slack app; `id` is used in secret names and message attribution |

Deploy with `./deployCDK.sh <env>`, which passes `--context env=<env>` to the CDK app.

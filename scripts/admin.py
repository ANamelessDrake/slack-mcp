#!/usr/bin/env python3
"""Operator CLI for a slack-mcp deployment.

    python scripts/admin.py register-agents --env dev [--profile NAME]
    python scripts/admin.py list-agents --env dev [--profile NAME]

register-agents reads each agent's Slack bot token from Secrets Manager, asks
Slack who that bot is (auth.test), and writes the agent registry items that
ingest uses to route @mentions to agent identities. Re-run it whenever an
agent app is added or reinstalled; ingest picks the registry up on its next
cold start. Agents whose bot token secret still holds a placeholder are
skipped with a warning.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]


def _load_config(env: str) -> dict:
    path = ROOT / "config" / f"{env}.json"
    if not path.is_file():
        sys.exit(f"config/{env}.json not found")
    return json.load(open(path))


def _session(profile: str | None):
    return boto3.Session(profile_name=profile) if profile else boto3.Session()


def _auth_test(bot_token: str) -> dict:
    req = urllib.request.Request(
        "https://slack.com/api/auth.test",
        headers={"Authorization": f"Bearer {bot_token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def register_agents(config: dict, session) -> None:
    prefix = f"{config['environment_name_upper']}-{config['project_name_upper']}"
    table_name = f"{config['environment_name']}-{config['project_name']}-messages"
    secrets = session.client("secretsmanager")
    table = session.resource("dynamodb").Table(table_name)

    for agent in config["agents"]:
        agent_id = agent["id"]
        secret_id = f"{prefix}-BotToken-{agent_id}"
        token = secrets.get_secret_value(SecretId=secret_id)["SecretString"]
        if not token.startswith("xoxb-"):
            print(f"skip {agent_id}: {secret_id} does not hold a bot token yet")
            continue
        info = _auth_test(token)
        if not info.get("ok"):
            print(f"skip {agent_id}: auth.test failed: {info.get('error')}")
            continue
        table.put_item(
            Item={
                "PK": "AGENTS",
                "SK": f"AGENT#{agent_id}",
                "agent_id": agent_id,
                "display_name": agent.get("display_name", agent_id),
                "bot_user_id": info.get("user_id", ""),
                "bot_name": info.get("user", ""),
            }
        )
        print(f"registered {agent_id}: bot_user_id={info.get('user_id')}")


def list_agents(config: dict, session) -> None:
    from boto3.dynamodb.conditions import Key

    table_name = f"{config['environment_name']}-{config['project_name']}-messages"
    table = session.resource("dynamodb").Table(table_name)
    items = table.query(KeyConditionExpression=Key("PK").eq("AGENTS")).get("Items", [])
    if not items:
        print("no agents registered")
    for item in items:
        print(f"{item['agent_id']}: bot_user_id={item.get('bot_user_id')} "
              f"display_name={item.get('display_name')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["register-agents", "list-agents"])
    parser.add_argument("--env", required=True)
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()

    config = _load_config(args.env)
    session = _session(args.profile)
    if args.command == "register-agents":
        register_agents(config, session)
    else:
        list_agents(config, session)


if __name__ == "__main__":
    main()

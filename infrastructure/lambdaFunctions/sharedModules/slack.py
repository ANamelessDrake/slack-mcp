"""Slack WebClient factories.

Each agent Slack app has its own bot token secret ({Env}-{Project}-BotToken-{agent_id});
the relay app's token is used for all read paths (DESIGN.md section 3). Clients and
secret values are cached for the lifetime of the Lambda container.
"""

import os
from functools import lru_cache

from slack_sdk import WebClient


@lru_cache(maxsize=32)
def _secret_value(name: str) -> str:
    import boto3

    return boto3.client("secretsmanager").get_secret_value(SecretId=name)["SecretString"]


@lru_cache(maxsize=1)
def relay_client() -> WebClient:
    """Client for the relay app: channel listing, history, thread reads."""
    return WebClient(token=_secret_value(os.environ["RELAY_BOT_TOKEN_SECRET"]))


@lru_cache(maxsize=8)
def agent_client(agent_id: str) -> WebClient:
    """Client for one agent app: message sends attributed to that agent."""
    prefix = os.environ["AGENT_TOKEN_SECRET_PREFIX"]
    return WebClient(token=_secret_value(f"{prefix}{agent_id}"))


def default_agent_id() -> str:
    """The agent identity bound to the milestone 1 static token."""
    return os.environ["DEFAULT_AGENT_ID"]

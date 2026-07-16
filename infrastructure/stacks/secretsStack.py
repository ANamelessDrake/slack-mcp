from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class SecretsStack(Stack):
    """Slack credentials and the milestone 1 dev bearer token.

    Every secret is created with a generated placeholder value. After creating the
    Slack apps (see docs/slack-app-setup.md), store the real tokens:

        aws secretsmanager put-secret-value --secret-id <name> --secret-string xoxb-...

    The DevBearerToken keeps its generated value; that is the token MCP clients
    present (single-tenant static-token auth, DESIGN.md section 4.1).
    """

    def __init__(self, scope: Construct, construct_id: str, *, config: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        prefix = f"{config['environment_name_upper']}-{config['project_name_upper']}"
        # Real Slack tokens should survive accidental stack deletion in prod
        self._removal_policy = (
            RemovalPolicy.RETAIN
            if config["environment_name"] == "prod"
            else RemovalPolicy.DESTROY
        )

        self.relay_bot_token = self._secret(
            f"{prefix}-RelayBotToken",
            "Bot token (xoxb-) for the relay Slack app (reads and event ingest)",
        )
        self.relay_signing_secret = self._secret(
            f"{prefix}-RelaySigningSecret",
            "Signing secret for the relay Slack app (verifies Events API requests, milestone 2)",
        )
        self.dev_bearer_token = self._secret(
            f"{prefix}-DevBearerToken",
            "Static bearer token for MCP clients (deployment-local auth)",
        )

        self.agent_bot_tokens: dict[str, secretsmanager.Secret] = {}
        self.agent_signing_secrets: dict[str, secretsmanager.Secret] = {}
        for agent in config["agents"]:
            agent_id = agent["id"]
            self.agent_bot_tokens[agent_id] = self._secret(
                f"{prefix}-BotToken-{agent_id}",
                f"Bot token (xoxb-) for the '{agent['display_name']}' agent Slack app",
            )
            # Needed once the agent app subscribes to message.im (two-way DMs):
            # Slack signs each app's event deliveries with that app's own secret.
            self.agent_signing_secrets[agent_id] = self._secret(
                f"{prefix}-SigningSecret-{agent_id}",
                f"Signing secret for the '{agent['display_name']}' agent Slack app (DM events)",
            )

    def _secret(self, name: str, description: str) -> secretsmanager.Secret:
        secret = secretsmanager.Secret(
            self,
            name,
            secret_name=name,
            description=description,
        )
        secret.apply_removal_policy(self._removal_policy)
        return secret

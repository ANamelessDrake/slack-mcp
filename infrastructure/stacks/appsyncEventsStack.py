from aws_cdk import Duration, Expiration, Stack
from aws_cdk import aws_appsync as appsync
from constructs import Construct


class AppSyncEventsStack(Stack):
    """Real-time pub/sub between ingest and waiting agent sessions.

    Ingest publishes each stored message to slack/messages/{channel_id}; a
    wait_for_messages call subscribes over WebSocket and unblocks the moment a
    message lands (DESIGN.md section 6). Delivery does not depend on this bus:
    the DynamoDB write in ingest is the durability guarantee, and this publish
    is best-effort fan-out to whoever is listening right now.

    Auth is a single API key held server-side by the two Lambdas (it is their
    credential, never a client's). The key expires after ~1 year; redeploying
    refreshes it. Migrating publish/subscribe to IAM-signed requests is the
    hardening path if this ever outgrows dev.
    """

    def __init__(self, scope: Construct, construct_id: str, *, config: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        env_project = f"{config['environment_name']}-{config['project_name']}"

        self.event_api = appsync.EventApi(
            self,
            "EventsApi",
            api_name=f"{env_project}-events",
            authorization_config=appsync.EventApiAuthConfig(
                auth_providers=[
                    appsync.AppSyncAuthProvider(
                        authorization_type=appsync.AppSyncAuthorizationType.API_KEY,
                        api_key_config=appsync.AppSyncApiKeyConfig(
                            description="Server-side key for ingest publish and session subscribe",
                            expires=Expiration.after(Duration.days(365)),
                        ),
                    )
                ],
            ),
        )

        self.namespace = self.event_api.add_channel_namespace(
            "SlackNamespace",
            channel_namespace_name="slack",
        )

        self.api_key = self.event_api.api_keys["Default"].attr_api_key
        self.http_endpoint = f"https://{self.event_api.http_dns}/event"
        self.realtime_endpoint = f"wss://{self.event_api.realtime_dns}/event/realtime"
        self.http_host = self.event_api.http_dns

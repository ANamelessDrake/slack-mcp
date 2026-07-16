from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct


class DynamoDbStack(Stack):
    """The message inbox (DESIGN.md section 5).

    One table holds both message items (PK=CH#<channel>, SK=TS#<ts>) and
    per-identity read cursors (PK=CURSOR#<identity>, SK=CH#<channel>). Message
    items expire via TTL after 30 days; cursors have no TTL.
    """

    def __init__(self, scope: Construct, construct_id: str, *, config: dict, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        env_project = f"{config['environment_name']}-{config['project_name']}"
        is_prod = config["environment_name"] == "prod"

        self.messages_table = dynamodb.Table(
            self,
            "Messages",
            table_name=f"{env_project}-messages",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY,
        )

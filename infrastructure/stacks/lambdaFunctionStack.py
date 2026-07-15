from aws_cdk import BundlingOptions, CfnOutput, Duration, Stack
from aws_cdk import aws_lambda as _lambda
from constructs import Construct

# Lambda Web Adapter public layer (see https://github.com/awslabs/aws-lambda-web-adapter)
LWA_ACCOUNT = "753240598075"
LWA_LAYER_NAME = "LambdaAdapterLayerArm64"
LWA_LAYER_VERSION = "25"


class LambdaFunctionsStack(Stack):
    """The MCP server Lambda behind a streaming Function URL.

    Streamable HTTP MCP needs SSE passthrough; API Gateway and ALB buffer responses,
    so the front door is a Function URL in RESPONSE_STREAM mode with Lambda Web
    Adapter running the ASGI app (DESIGN.md section 2).
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: dict,
        secrets,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        env_project = f"{config['environment_name']}-{config['project_name']}"
        secret_prefix = f"{config['environment_name_upper']}-{config['project_name_upper']}"

        lwa_layer = _lambda.LayerVersion.from_layer_version_arn(
            self,
            "LambdaWebAdapter",
            f"arn:aws:lambda:{self.region}:{LWA_ACCOUNT}:layer:{LWA_LAYER_NAME}:{LWA_LAYER_VERSION}",
        )

        # Asset root is lambdaFunctions/ so sharedModules/ can be bundled alongside
        # the function's own code.
        code = _lambda.Code.from_asset(
            "infrastructure/lambdaFunctions",
            bundling=BundlingOptions(
                image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                command=[
                    "bash",
                    "-c",
                    " && ".join(
                        [
                            "pip install -r mcpServer/requirements.txt -t /asset-output",
                            "cp -r mcpServer/. /asset-output/",
                            "cp -r sharedModules /asset-output/sharedModules",
                            "chmod +x /asset-output/run.sh",
                        ]
                    ),
                ],
            ),
        )

        self.mcp_function = _lambda.Function(
            self,
            "McpServer",
            function_name=f"{env_project}-mcp-server",
            description="Streamable HTTP MCP server for Slack messaging",
            runtime=_lambda.Runtime.PYTHON_3_12,
            architecture=_lambda.Architecture.ARM_64,
            handler="run.sh",
            code=code,
            layers=[lwa_layer],
            memory_size=512,
            timeout=Duration.seconds(120),
            environment={
                "AWS_LAMBDA_EXEC_WRAPPER": "/opt/bootstrap",
                "AWS_LWA_INVOKE_MODE": "response_stream",
                "PORT": "8000",
                "RELAY_BOT_TOKEN_SECRET": secrets.relay_bot_token.secret_name,
                "DEV_BEARER_TOKEN_SECRET": secrets.dev_bearer_token.secret_name,
                "AGENT_TOKEN_SECRET_PREFIX": f"{secret_prefix}-BotToken-",
                "DEFAULT_AGENT_ID": config["default_agent_id"],
            },
        )

        secrets.relay_bot_token.grant_read(self.mcp_function)
        secrets.dev_bearer_token.grant_read(self.mcp_function)
        for secret in secrets.agent_bot_tokens.values():
            secret.grant_read(self.mcp_function)

        self.function_url = self.mcp_function.add_function_url(
            auth_type=_lambda.FunctionUrlAuthType.NONE,
            invoke_mode=_lambda.InvokeMode.RESPONSE_STREAM,
        )

        CfnOutput(
            self,
            "McpEndpoint",
            value=f"{self.function_url.url}mcp",
            description="MCP endpoint URL for client configuration",
        )

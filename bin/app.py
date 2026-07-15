#!/usr/bin/env python3
import json
import os
import sys

import aws_cdk as cdk
from aws_cdk import Environment

# Add the project root to the Python path so infrastructure/ imports resolve
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(project_root)

from infrastructure.stacks.lambdaFunctionStack import LambdaFunctionsStack  # noqa: E402
from infrastructure.stacks.secretsStack import SecretsStack  # noqa: E402

app = cdk.App()

# Load the environment from the context
environment = app.node.try_get_context("env")
if not environment:
    print("Error: No environment specified. Use --context env=<value>.")
    sys.exit(1)

# Load the matching config file
config_path = os.path.join(project_root, "config", f"{environment}.json")
try:
    with open(config_path) as f:
        config = json.load(f)
except FileNotFoundError:
    print(f"Configuration file '{config_path}' not found. Copy config/example.json to start.")
    sys.exit(1)

env = Environment(account=config["account"], region=config.get("region", "us-east-1"))
env_project = f"{config['environment_name']}-{config['project_name']}"

secrets_stack = SecretsStack(
    app,
    f"{env_project}-secrets",
    config=config,
    env=env,
)

lambda_stack = LambdaFunctionsStack(
    app,
    f"{env_project}-lambda",
    config=config,
    secrets=secrets_stack,
    env=env,
)
lambda_stack.add_dependency(secrets_stack)

app.synth()

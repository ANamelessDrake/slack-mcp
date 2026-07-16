import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAMBDA_DIR = ROOT / "infrastructure" / "lambdaFunctions"

# Mirror the deployed bundle layout: function code at the root, sharedModules beside it
sys.path.insert(0, str(LAMBDA_DIR))
sys.path.insert(0, str(LAMBDA_DIR / "mcpServer"))

# Local-mode auth and identity, set before app/auth modules are imported
os.environ.setdefault("DEV_BEARER_TOKEN", "test-token")
os.environ.setdefault("DEFAULT_AGENT_ID", "wilma")
os.environ.setdefault("AGENT_TOKEN_SECRET_PREFIX", "Test-SlackMcp-BotToken-")

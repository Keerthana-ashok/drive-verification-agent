#!/usr/bin/env python3
"""CDK entry point: two copies of the system (staging, prod) plus the GitHub connection."""
import os

import aws_cdk as cdk

from stacks.agent_stack import AgentStack
from stacks.github_oidc_stack import GitHubOidcStack

app = cdk.App()
env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
                      region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"))

HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
model_id = app.node.try_get_context("model_id") or HAIKU

AgentStack(app, "DriveAgent-Staging", stage="staging", model_id=model_id, canary=False, env=env)
AgentStack(app, "DriveAgent-Prod", stage="prod", model_id=model_id, canary=True, env=env)
GitHubOidcStack(app, "DriveAgent-GitHub", github_repo=app.node.try_get_context("github_repo"), env=env)

cdk.Tags.of(app).add("project", "drive-verification-agent")
app.synth()

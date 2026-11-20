"""Lets GitHub Actions deploy to AWS without storing any access keys.

GitHub proves which repo and branch is running (OIDC token); AWS hands back
short-lived credentials for this role only. The role can only assume the
CDK deployment roles plus the few Bedrock calls the pipeline needs.
"""
from aws_cdk import CfnOutput, Duration, Stack, aws_iam as iam
from constructs import Construct


class GitHubOidcStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, github_repo: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        provider = iam.OpenIdConnectProvider(
            self, "GitHubProvider", url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
        )
        role = iam.Role(
            self, "DeployRole",
            role_name="github-actions-drive-agent-deploy",
            max_session_duration=Duration.hours(1),
            assumed_by=iam.WebIdentityPrincipal(
                provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
                    "StringLike": {"token.actions.githubusercontent.com:sub": f"repo:{github_repo}:*"},
                },
            ),
        )
        # CDK deploys by assuming the roles created by `cdk bootstrap`.
        role.add_to_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{self.account}:role/cdk-hnb659fds-*"]))
        # Read stack outputs, sync the knowledge base, run the evaluation set.
        role.add_to_policy(iam.PolicyStatement(
            actions=["cloudformation:DescribeStacks"],
            resources=[f"arn:aws:cloudformation:{self.region}:{self.account}:stack/DriveAgent-*/*"]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:StartIngestionJob", "bedrock:GetIngestionJob", "bedrock:Retrieve",
                     "bedrock:ApplyGuardrail", "bedrock:InvokeModel"],
            resources=["*"]))

        CfnOutput(self, "DeployRoleArn", value=role.role_arn)

"""Print a stack's outputs, or write them into the GitHub Actions environment.

Usage:
  python scripts/stack_outputs.py DriveAgent-Staging
  python scripts/stack_outputs.py DriveAgent-Staging --github-env
"""
import argparse
import os

import boto3


def get_outputs(stack: str, region: str = "us-east-1") -> dict:
    cfn = boto3.client("cloudformation", region_name=os.environ.get("AWS_REGION", region))
    outputs = cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("stack")
    p.add_argument("--github-env", action="store_true")
    args = p.parse_args()
    outputs = get_outputs(args.stack)
    lines = [f"{k}={v}" for k, v in outputs.items()]
    if args.github_env and os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

"""Create the two demo logins (a support rep and a reviewer) in a stack's user pool.

Usage: python scripts/create_users.py DriveAgent-Staging
Passwords are printed once; change them with --rep-password / --reviewer-password.
"""
import argparse
import secrets

import boto3
from botocore.exceptions import ClientError

from stack_outputs import get_outputs


def make_password() -> str:
    return f"Demo-{secrets.token_urlsafe(8)}9a"


def ensure_user(client, pool: str, email: str, password: str, group: str) -> None:
    try:
        client.admin_create_user(
            UserPoolId=pool, Username=email, MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "UsernameExistsException":
            raise
    client.admin_set_user_password(UserPoolId=pool, Username=email, Password=password, Permanent=True)
    client.admin_add_user_to_group(UserPoolId=pool, Username=email, GroupName=group)
    print(f"  {group:<10} {email:<24} password: {password}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("stack")
    p.add_argument("--rep-email", default="rep@example.com")
    p.add_argument("--reviewer-email", default="reviewer@example.com")
    p.add_argument("--rep-password", default=make_password())
    p.add_argument("--reviewer-password", default=make_password())
    args = p.parse_args()

    out = get_outputs(args.stack)
    client = boto3.client("cognito-idp", region_name="us-east-1")
    print(f"Demo users for {args.stack} ({out['WebsiteUrl']}):")
    ensure_user(client, out["UserPoolId"], args.rep_email, args.rep_password, "reps")
    ensure_user(client, out["UserPoolId"], args.reviewer_email, args.reviewer_password, "reviewers")


if __name__ == "__main__":
    main()

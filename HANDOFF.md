# Handoff: drive-verification-agent

Paste this file into GitHub Copilot Chat (or keep it in the repo and ask Copilot to read `HANDOFF.md`) so it has the full context.

## What this project is

A portfolio AI agent for drive support reps: checks authenticity (deterministic mock), warranty (computed in code), retrieves policies with RAG (Bedrock Knowledge Base on S3 Vectors), and has Claude write a cited recommendation through a forced tool call. RMA replacements and counterfeit flags go to a human reviewer. Everything is audited in DynamoDB. All data is fictional ("Example Drives").

- Code: `app/` (LangGraph agent in `app/agent/graph.py`, Lambda handler in `app/handler.py`)
- Infra: `infra/` (AWS CDK, Python). Stacks: `DriveAgent-Staging`, `DriveAgent-Prod`, `DriveAgent-GitHub` (don't deploy this one, see below)
- Release: `scripts/release.sh` (tests, then staging, then eval gate, then manual "yes", then prod with a 10% canary and auto rollback)
- Full guide: `docs/SETUP.md`

## Constraints of this AWS account (important)

The account was created with AWS's new sign-up flow. It's a restricted sandbox account in an AWS-managed organization:

- Console sign-in is a temporary role: `assumed-role/AccountFullAccessRole/...`. There is no root password flow to rely on.
- **Creating IAM users with console passwords is blocked** by a service control policy (SCP). Not needed.
- **GitHub OIDC is blocked** (`iam:ListOpenIDConnectProviders` denied by SCP). Don't deploy `DriveAgent-GitHub`. GitHub Actions runs CI only; the deploy jobs in `.github/workflows/deploy.yml` stay off unless the repo variable `AWS_DEPLOY_ROLE_ARN` is set.
- **Creating IAM roles works** (tested), so Lambda and the other services can be deployed.
- Bedrock models: **Claude Haiku 4.5 and Claude Sonnet 4.5 work.** Claude Haiku 5.5 and newer returned "not available for this account". The default model in `infra/app.py` is `us.anthropic.claude-haiku-4-5-20251001-v1:0`. Sonnet 4.5 ID for comparison: `us.anthropic.claude-sonnet-4-5-20250929-v1:0`.
- Region: **us-east-1** for everything.
- Don't enable IAM Identity Center or AWS Organizations, and don't create IAM access keys. On the Free plan, creating an organization upgrades the account to Paid and expires the free credits.
- Avoid OpenSearch Serverless and NAT gateways (expensive even when idle). The design already uses S3 Vectors and no VPC.
- If any deploy step fails with "explicit deny in a service control policy", that service is blocked by the account. Swap it for an allowed one instead of trying to work around the policy.

## Where we stopped

Done:
- AWS account set up, Bedrock playground tested (Haiku 4.5, Sonnet 4.5), $10 budget alert created.
- Code is on GitHub: `https://github.com/Keerthana-ashok/drive-verification-agent`
- Repo cloned on the Mac and opened in VS Code; Homebrew tools (python@3.12, node, awscli, git, aws-cdk) were installing.
- The unused IAM user `KeerthanaAshok` can be deleted (IAM, then Users, then Delete).

Not done yet: everything from "log the Mac into AWS" onward.

## Next steps (macOS, run in the VS Code terminal from the repo folder)

### 1. Python environment and unit tests

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q            # expect 13 passed
```

### 2. Log the Mac into AWS (no access keys)

```bash
aws --version                 # needs aws-cli/2.32.0 or newer; else: brew upgrade awscli
aws login --profile drive     # browser opens; sign in like the console; region us-east-1
export AWS_PROFILE=drive
export AWS_REGION=us-east-1
aws sts get-caller-identity   # expect assumed-role/AccountFullAccessRole/...
echo 'export AWS_PROFILE=drive' >> ~/.zshrc
echo 'export AWS_REGION=us-east-1' >> ~/.zshrc
```

If CDK or Python can't find credentials after `aws login`:

```bash
eval "$(aws configure export-credentials --profile drive --format env)"
```

If `aws login` doesn't work for this account type, a fallback is to run the deploy commands in **AWS CloudShell** (console, bottom left): clone the repo there, install CDK with `npm install -g aws-cdk`, and use Python 3.12 if available.

### 3. First deploy to staging

```bash
python scripts/build_lambda.py               # builds build/lambda (~75 MB), no Docker needed
cd infra
cdk bootstrap
cdk deploy DriveAgent-Staging                # 10-20 min first time; type y to approve IAM changes
cd ..
python scripts/ingest.py DriveAgent-Staging  # loads policies into the knowledge base
python scripts/create_users.py DriveAgent-Staging   # prints demo logins + passwords
python scripts/stack_outputs.py DriveAgent-Staging  # WebsiteUrl, ApiUrl, etc.
```

### 4. Try it

- Open `WebsiteUrl`, sign in as `rep@example.com`, use the "Try" buttons.
- Sign in as `reviewer@example.com`, then open the Approval queue and approve a case with a reason.
- Run the eval gate: `python eval/run_eval.py --stack DriveAgent-Staging`
- Compare models: add `--model us.anthropic.claude-sonnet-4-5-20250929-v1:0`

### 5. Release to production

```bash
git add . && git commit -m "Your change"
git push
./scripts/release.sh          # type yes at the approval prompt
python scripts/create_users.py DriveAgent-Prod
```

### 6. Tear down when finished (stops charges)

```bash
cd infra
cdk destroy DriveAgent-Prod DriveAgent-Staging
```

Then delete the `CDKToolkit` stack in CloudFormation (empty its `cdk-...` S3 bucket first).

## Things that may need fixing on first deploy

The code was tested with unit tests and `cdk synth`, and the templates pass `cfn-lint`. It has not been deployed to a real account yet. Likely trouble spots:

- **SCP denies** on a specific service (CloudFront, Cognito, CodeDeploy, S3 Vectors): check the error, then swap or remove that piece.
- **Knowledge Base on S3 Vectors** (`infra/stacks/agent_stack.py`): if creation fails, check the index metadata config and the KB role permissions.
- **Model access from Lambda**: if `AccessDeniedException`, confirm the model ID in `infra/app.py` matches one that works in the Bedrock playground.
- **Guardrail**: if the `NAME` PII filter masks too much, remove it from the list in `agent_stack.py`.
- Logs: CloudWatch, then Log groups, then the `...ApiLogs...` group shows the real error behind "Something went wrong".

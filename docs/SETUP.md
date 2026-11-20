# Setup guide (macOS)

This takes about 1.5–2 hours the first time, most of it waiting for AWS. Every command is meant to be copied and pasted into **Terminal**. Lines starting with `#` are comments.

You'll do it in this order:

1. [Check your AWS account](#1-check-your-aws-account) (5 min)
2. [Install tools on your Mac and log in](#2-install-tools-on-your-mac-and-log-in) (15 min)
3. [Put the code on GitHub](#3-put-the-code-on-github) (10 min)
4. [First deploy to staging, by hand](#4-first-deploy-to-staging-by-hand) (20–30 min)
5. [Try it in the browser](#5-try-it-in-the-browser) (10 min)
6. [Release to production](#6-release-to-production) (20 min)
7. [Optional: watch an automatic rollback](#7-optional-watch-an-automatic-rollback)
8. [Tear everything down](#8-tear-everything-down) (when your week is over)

---

## 1. Check your AWS account

New AWS sign-ups create a restricted account. You sign in through a temporary role (`AccountFullAccessRole`), and some actions are blocked by AWS-managed rules:

- **You can't create IAM users with console passwords.** You don't need one. Your temporary-role sign-in is already the secure setup.
- **GitHub can't connect to AWS (OIDC is blocked).** So releases to staging and prod run from your Mac with `scripts/release.sh`, which runs the same stages as a pipeline. GitHub Actions still runs the tests on every push.
- **Creating IAM roles works**, which is what Lambda and the other services need.

Before starting:
- Region (top right of the console): **US East (N. Virginia) us-east-1**. Use it for everything.
- You already confirmed **Claude Haiku 4.5** and **Claude Sonnet 4.5** answer in the Bedrock playground.
- Don't enable IAM Identity Center or AWS Organizations.

---

## 2. Install tools on your Mac and log in

Open **Terminal** (Cmd + Space, type Terminal).

```bash
# Homebrew (the Mac package manager). Skip if `brew --version` already works.
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
# If it tells you to run two "echo ... >> ~/.zprofile" lines at the end, run them, then close and reopen Terminal.

# Python 3.12, Node.js, AWS CLI, Git
brew install python@3.12 node awscli git

# AWS CDK command-line tool
npm install -g aws-cdk

# Check versions
python3.12 --version     # Python 3.12.x
node --version           # v20 or newer
aws --version            # must be aws-cli/2.32.0 or newer (needed for `aws login`)
cdk --version
```

No Docker needed: the build script downloads Linux packages for Lambda directly.

### Log the AWS CLI in (no access keys)

```bash
aws login --profile drive
# A browser opens. Sign in the same way you sign in to the console.
# When asked for a region, type us-east-1.

export AWS_PROFILE=drive
export AWS_REGION=us-east-1
aws sts get-caller-identity
# You should see your account ID and "assumed-role/AccountFullAccessRole/...".
```

If `aws login` doesn't work with your account, stop here and paste the error to Claude; there's a fallback.

Add the two `export` lines to `~/.zshrc` so every new Terminal window uses them:

```bash
echo 'export AWS_PROFILE=drive' >> ~/.zshrc
echo 'export AWS_REGION=us-east-1' >> ~/.zshrc
```

The login lasts a few hours. When commands start failing with "expired" errors, run `aws login --profile drive` again.

---

## 3. Put the code on GitHub

1. On github.com, click **+** → **New repository**. Name: `drive-verification-agent`. Choose **Public** (it's your portfolio). Don't add a README. **Create repository**.
2. In Terminal (adjust the path to where you unzipped the project):

```bash
cd ~/Downloads/drive-verification-agent
git init
git add .
git commit -m "Initial commit: drive verification AI agent"
git branch -M main
git remote add origin https://github.com/Keerthana-ashok/drive-verification-agent.git
git push -u origin main
```

GitHub will ask you to sign in the first time. The easiest way is the GitHub CLI: `brew install gh && gh auth login`, then push again.

On github.com → **Actions**, you'll see the **test** job run: unit tests plus an infrastructure check. That's your CI.

---

## 4. First deploy to staging, by hand

Doing the first deploy step by step means you see each piece being created.

```bash
cd ~/Downloads/drive-verification-agent

# Python environment for this project
python3.12 -m venv .venv
source .venv/bin/activate          # run this again in every new Terminal window
pip install -r requirements-dev.txt

# Unit tests (no AWS calls)
pytest -q                           # expect: 13 passed

# Package the Lambda code
python scripts/build_lambda.py      # "Built build/lambda (~75 MB)"

# One-time: prepare your account for CDK
cd infra
cdk bootstrap

# Deploy staging (first time takes 10–20 minutes; CloudFront is the slow part)
cdk deploy DriveAgent-Staging
# Type y when it lists the security changes.
cd ..

# Load the policy documents into the knowledge base
python scripts/ingest.py DriveAgent-Staging

# Create the demo logins (save the passwords it prints)
python scripts/create_users.py DriveAgent-Staging

# See the URLs and IDs
python scripts/stack_outputs.py DriveAgent-Staging
```

Look in the AWS console while it runs: **CloudFormation** shows each resource being created. If any step fails with an "explicit deny in a service control policy" error, paste it to Claude: it means your account blocks that service, and we'll swap it.

---

## 5. Try it in the browser

1. Open the `WebsiteUrl` from the outputs.
2. Sign in as `rep@example.com`. Use the **Try** buttons:
   - **In warranty** → RMA_REPLACEMENT, status PENDING_REVIEW
   - **Expired** → NONE, offers out-of-warranty options
   - **Counterfeit** → FLAG_COUNTERFEIT
   - **No purchase date** → REQUEST_INFO (no LLM call)
   - **FIPS question** → NONE, cites the FIPS policy
3. Sign out, sign in as `reviewer@example.com` → **Approval queue** → approve one case with a reason.
4. In the console, look at **DynamoDB → Tables → ...AuditLog... → Explore items**: every case and decision is recorded with the model and prompt version.

Run the evaluation set from your Mac:

```bash
python eval/run_eval.py --stack DriveAgent-Staging
# Compare with Sonnet 4.5 (an interview talking point: quality vs cost)
python eval/run_eval.py --stack DriveAgent-Staging --model us.anthropic.claude-sonnet-4-5-20250929-v1:0
```

---

## 6. Release to production

From now on, every release goes through one script:

```bash
git add . && git commit -m "Describe your change"    # releases must map to a commit
git push                                              # GitHub Actions runs the tests
./scripts/release.sh
```

It runs these stages and stops at the first failure:

1. Unit tests
2. Build the Lambda package
3. Deploy staging, load policies, smoke test
4. **AI evaluation gate**: fails the release if fewer than 90% of cases get the right action or 80% cite the right policy
5. **Manual approval**: type `yes` to release to production
6. Deploy prod: CodeDeploy sends 10% of traffic to the new version for 5 minutes, rolls back automatically on errors, then smoke test

The first prod deploy creates everything, so there's no canary yet. Every later release uses it.

Then create prod logins and try prod:

```bash
python scripts/create_users.py DriveAgent-Prod
python scripts/stack_outputs.py DriveAgent-Prod
```

### See a canary in action

Make a small change, for example set `PROMPT_VERSION = "v2"` in `app/agent/llm.py`, commit, and run `./scripts/release.sh`. While stage 6 runs, open **CodeDeploy → Deployments** in the console to watch traffic shift from 10% to 100%.

---

## 7. Optional: watch an automatic rollback

1. In `app/handler.py`, make `GET /health` raise an error: add `raise RuntimeError("test rollback")` as the first line inside `if route == "GET /health":`.
2. Commit and run `./scripts/release.sh`. The staging smoke test will fail, which is the pipeline doing its job. To test the prod rollback specifically, deploy prod directly: `cd infra && cdk deploy DriveAgent-Prod`.
3. While CodeDeploy shows the canary, in a second Terminal window send traffic so the error alarm fires:
   ```bash
   API=$(python scripts/stack_outputs.py DriveAgent-Prod | grep ^ApiUrl= | cut -d= -f2)
   for i in {1..60}; do curl -s "${API}health"; echo; sleep 3; done
   ```
4. CodeDeploy rolls back and CloudFormation reverts. Prod keeps serving the previous version.
5. Remove the `raise` line, commit, and release again.

This is a strong interview story: "I tested the rollback path on purpose."

---

## 8. Tear everything down

When you're done (to stop all charges):

```bash
cd infra
cdk destroy DriveAgent-Prod DriveAgent-Staging
# Type y for each.
```

Then in the console:
- **CloudFormation**: delete the `CDKToolkit` stack if you won't use CDK again (empty its S3 bucket first: S3 → bucket starting with `cdk-` → Empty).
- **Bedrock → Knowledge bases** and **S3**: check nothing from this project is left.
- **Billing → Bills**: check charges over the next day or two.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `aws: error: argument command: Invalid choice: 'login'` | Your AWS CLI is too old: `brew upgrade awscli` |
| CDK or Python says it can't find credentials | Run `export AWS_PROFILE=drive`, then `aws login --profile drive`. If it still fails: `eval "$(aws configure export-credentials --profile drive --format env)"` |
| "explicit deny in a service control policy" | Your account blocks that action. Paste the full error to Claude. |
| Website shows "Something went wrong" | CloudWatch → Log groups → `...ApiLogs...` shows the real error |
| Answers have no policy citations | You skipped `python scripts/ingest.py <stack>` after deploying |
| `AccessDeniedException` calling the model | The model ID isn't available to your account. Use the Haiku 4.5 ID in `infra/app.py` |

#!/usr/bin/env bash
# Release from your Mac: the same stages as a CI/CD pipeline.
#   tests -> build -> deploy staging -> sync KB -> smoke test -> AI eval gate
#   -> manual approval -> deploy prod (10% canary, auto rollback) -> sync KB -> smoke test
# Usage: ./scripts/release.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -d .git ] && ! git diff --quiet; then
  echo "You have uncommitted changes. Commit them first so every release maps to a commit."
  exit 1
fi
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "no-git")
echo "Releasing commit ${COMMIT}"

source .venv/bin/activate

echo; echo "== 1/6 Unit tests"
pytest -q

echo; echo "== 2/6 Build Lambda package"
python scripts/build_lambda.py

echo; echo "== 3/6 Deploy staging"
(cd infra && cdk deploy DriveAgent-Staging --require-approval never)
python scripts/ingest.py DriveAgent-Staging
python scripts/smoke_test.py DriveAgent-Staging

echo; echo "== 4/6 AI evaluation gate (stops the release if quality drops)"
python eval/run_eval.py --stack DriveAgent-Staging

echo; echo "== 5/6 Manual approval"
read -r -p "Staging passed the eval gate. Release commit ${COMMIT} to PRODUCTION? Type yes: " answer
if [ "${answer}" != "yes" ]; then
  echo "Release cancelled. Nothing was changed in prod."
  exit 1
fi

echo; echo "== 6/6 Deploy prod (CodeDeploy: 10% of traffic for 5 minutes, rolls back on errors)"
(cd infra && cdk deploy DriveAgent-Prod --require-approval never)
python scripts/ingest.py DriveAgent-Prod
python scripts/smoke_test.py DriveAgent-Prod

echo; echo "Release of ${COMMIT} complete."
python scripts/stack_outputs.py DriveAgent-Prod | grep -E "^(WebsiteUrl|ApiUrl)="

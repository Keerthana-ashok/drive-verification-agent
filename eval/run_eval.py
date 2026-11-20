"""The AI quality gate. Runs known cases against a deployed stack's model,
knowledge base and guardrail, and fails (exit code 1) if quality drops.

Usage:
  python eval/run_eval.py --stack DriveAgent-Staging
  python eval/run_eval.py --stack DriveAgent-Staging --model us.anthropic.claude-sonnet-4-5-20250929-v1:0
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from stack_outputs import get_outputs  # noqa: E402

ACTION_THRESHOLD = 0.9   # share of cases with the right action
SOURCE_THRESHOLD = 0.8   # share of cases that cite the right policy file
AS_OF_DATE = "2026-11-19"  # fixed "today" so warranty dates don't drift


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stack", required=True)
    p.add_argument("--model", help="Override the model, e.g. to compare Haiku with Sonnet")
    args = p.parse_args()

    out = get_outputs(args.stack)
    os.environ.update({
        "MODEL_ID": args.model or out["ModelId"],
        "KNOWLEDGE_BASE_ID": out["KnowledgeBaseId"],
        "GUARDRAIL_ID": out["GuardrailId"],
        "GUARDRAIL_VERSION": out["GuardrailVersion"],
        "AS_OF_DATE": AS_OF_DATE,
        "STAGE": "eval",
    })
    from app.agent.graph import run_case  # import after env is set

    cases = [json.loads(line) for line in (ROOT / "eval" / "cases.jsonl").read_text().splitlines() if line.strip()]
    rows, action_hits, source_hits, source_total, tokens = [], 0, 0, 0, 0
    for c in cases:
        start = time.time()
        result = run_case(c["serial"], c["question"])
        seconds = time.time() - start
        rec = result["recommendation"]
        action = "BLOCKED" if result["status"] == "BLOCKED" else rec.get("recommended_action")
        action_ok = action == c["expect_action"]
        action_hits += action_ok
        source_ok = None
        if c["expect_source"]:
            source_total += 1
            cited = [x.get("source", "") for x in rec.get("citations", [])]
            source_ok = any(c["expect_source"] in s for s in cited)
            source_hits += source_ok
        usage = result["trace"].get("token_usage") or {}
        tokens += int(usage.get("totalTokens", 0))
        rows.append({"id": c["id"], "expected": c["expect_action"], "got": action, "action_ok": action_ok,
                     "source_ok": source_ok, "seconds": round(seconds, 1), "overrides": result["overrides"]})
        print(f"{'PASS' if action_ok and source_ok is not False else 'FAIL'}  {c['id']}: expected {c['expect_action']}, "
              f"got {action}, source_ok={source_ok}, {seconds:.1f}s")

    action_rate = action_hits / len(cases)
    source_rate = source_hits / source_total if source_total else 1.0
    passed = action_rate >= ACTION_THRESHOLD and source_rate >= SOURCE_THRESHOLD
    report = {"model": os.environ["MODEL_ID"], "stack": args.stack, "action_accuracy": round(action_rate, 3),
              "source_accuracy": round(source_rate, 3), "total_tokens": tokens, "passed": passed, "cases": rows}
    (ROOT / "eval" / "report.json").write_text(json.dumps(report, indent=2))

    summary = (f"## Evaluation gate: {'PASSED' if passed else 'FAILED'}\n\n"
               f"Model: `{report['model']}`\n\n"
               f"| Metric | Result | Threshold |\n|---|---|---|\n"
               f"| Right action | {action_rate:.0%} | {ACTION_THRESHOLD:.0%} |\n"
               f"| Right policy cited | {source_rate:.0%} | {SOURCE_THRESHOLD:.0%} |\n"
               f"| Tokens used | {tokens} | |\n")
    print("\n" + summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()

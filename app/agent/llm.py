"""The one place the LLM is used: write a cited recommendation from tool results.

The model must answer through a forced tool call, so the output is always
structured JSON (verdict, reasons, citations, action), never free text.
"""
from __future__ import annotations

import json

import boto3

from .config import get_settings

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You help support reps at Example Drives answer customer questions about drives.

Rules you must follow:
1. The authenticity result and warranty facts in the case were computed by trusted systems. Never change, doubt or re-decide them.
2. Base every policy statement only on the policy excerpts provided. Cite the source file for each one.
3. Choose recommended_action:
   - RMA_REPLACEMENT only if the drive is GENUINE, IN_WARRANTY, and the customer reports a failure or defect.
   - FLAG_COUNTERFEIT if authenticity is NOT_VERIFIED.
   - REQUEST_INFO if a fact needed to answer is missing.
   - NONE otherwise (for example, a question that only needs information, or an expired warranty).
4. Write for a support rep: short, plain, and specific. Never accuse the customer of fraud.
"""

RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "description": "One-sentence answer for the support rep."},
        "reasons": {"type": "array", "items": {"type": "string"}, "description": "2 to 4 short reasons."},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Policy file name, e.g. warranty-policy.md"},
                    "section": {"type": "string", "description": "Section label, e.g. Section W3"},
                },
                "required": ["source", "section"],
            },
        },
        "recommended_action": {
            "type": "string",
            "enum": ["NONE", "RMA_REPLACEMENT", "FLAG_COUNTERFEIT", "REQUEST_INFO"],
        },
        "customer_message": {"type": "string", "description": "What the rep can tell the customer."},
    },
    "required": ["verdict", "reasons", "citations", "recommended_action", "customer_message"],
}


def _build_user_message(question: str, authenticity: dict, warranty: dict, policies: list[dict]) -> str:
    excerpts = "\n\n".join(f"[source: {p['source']}]\n{p['text']}" for p in policies) or "(no policy excerpts found)"
    return (
        f"Customer question: {question}\n\n"
        f"Authenticity result (trusted): {json.dumps(authenticity)}\n"
        f"Warranty and product facts (trusted): {json.dumps(warranty)}\n\n"
        f"Policy excerpts:\n{excerpts}\n\n"
        "Submit your recommendation with the submit_recommendation tool."
    )


def draft_recommendation(question: str, authenticity: dict, warranty: dict, policies: list[dict]) -> dict:
    settings = get_settings()
    client = boto3.client("bedrock-runtime", region_name=settings.region)
    resp = client.converse(
        modelId=settings.model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": _build_user_message(question, authenticity, warranty, policies)}]}],
        inferenceConfig={"maxTokens": 800, "temperature": 0},
        toolConfig={
            "tools": [
                {
                    "toolSpec": {
                        "name": "submit_recommendation",
                        "description": "Submit the structured recommendation for the support rep.",
                        "inputSchema": {"json": RECOMMENDATION_SCHEMA},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": "submit_recommendation"}},
        },
    )
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            result = dict(block["toolUse"]["input"])
            result["usage"] = resp.get("usage", {})
            return result
    raise RuntimeError("Model did not return a structured recommendation")

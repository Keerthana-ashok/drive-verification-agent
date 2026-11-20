"""The AI agent workflow, built with LangGraph.

The order of steps is fixed in code:

    guard -> verify -> warranty -> policy -> draft -> finalize
              |          |
              +----------+--> missing_info (no LLM call)

Code decides facts (authenticity, warranty dates) and has the final say on the
action in `finalize`. The LLM appears in one node only (`draft`).
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import guardrails, llm, tools
from .config import get_settings

WARRANTY_WORDS = ("warranty", "replace", "replacement", "rma", "fail", "broken", "dead", "defect", "return", "refund")
APPROVAL_ACTIONS = {"RMA_REPLACEMENT", "FLAG_COUNTERFEIT"}


class CaseState(TypedDict, total=False):
    serial: str
    question: str
    safe_question: str
    blocked: bool
    pii_masked: bool
    authenticity: dict
    warranty: dict
    policies: list
    recommendation: dict
    overrides: list
    needs_approval: bool
    status: str


def guard(state: CaseState) -> CaseState:
    result = guardrails.check_input(state["question"])
    return {"blocked": result["blocked"], "safe_question": result["text"], "pii_masked": result["masked"]}


def verify(state: CaseState) -> CaseState:
    return {"authenticity": tools.verify_authenticity(state["serial"])}


def warranty(state: CaseState) -> CaseState:
    return {"warranty": tools.lookup_warranty(state["serial"])}


def policy(state: CaseState) -> CaseState:
    w = state.get("warranty", {})
    query = (
        f"{state['safe_question']} "
        f"(authenticity: {state['authenticity']['status']}, warranty: {w.get('status')}, "
        f"refurbished: {w.get('refurbished')}, sku: {w.get('sku')})"
    )
    return {"policies": tools.search_policies(query)}


def draft(state: CaseState) -> CaseState:
    rec = llm.draft_recommendation(
        state["safe_question"], state["authenticity"], state.get("warranty", {}), state.get("policies", [])
    )
    return {"recommendation": rec}


def missing_info(state: CaseState) -> CaseState:
    if state["authenticity"]["status"] == "UNKNOWN_SERIAL":
        verdict = f"Serial number {state['serial']} was not found."
        ask = "Please confirm the serial number printed on the drive label."
    else:
        verdict = "No purchase date is on record, so warranty coverage can't be calculated yet."
        ask = "Please send proof of purchase (an invoice or receipt showing the date)."
    return {
        "recommendation": {
            "verdict": verdict,
            "reasons": ["A fact needed for a decision is missing; the agent asks instead of guessing."],
            "citations": [{"source": "warranty-policy.md", "section": "Section W2"}]
            if state["authenticity"]["status"] != "UNKNOWN_SERIAL"
            else [],
            "recommended_action": "REQUEST_INFO",
            "customer_message": ask,
        }
    }


def blocked_response(state: CaseState) -> CaseState:
    return {
        "recommendation": {
            "verdict": "This request was blocked by the safety filter.",
            "reasons": ["The message looked like an attempt to override the assistant's instructions."],
            "citations": [],
            "recommended_action": "NONE",
            "customer_message": "Please rephrase the question about your drive.",
        },
        "status": "BLOCKED",
        "needs_approval": False,
    }


def finalize(state: CaseState) -> CaseState:
    """Code has the last word: the LLM can't approve what the facts don't allow."""
    rec = dict(state["recommendation"])
    auth = state["authenticity"]["status"]
    w_status = state.get("warranty", {}).get("status")
    overrides = []

    if auth == "NOT_VERIFIED" and rec["recommended_action"] != "FLAG_COUNTERFEIT":
        overrides.append(f"Changed {rec['recommended_action']} to FLAG_COUNTERFEIT: authenticity check failed.")
        rec["recommended_action"] = "FLAG_COUNTERFEIT"
    if rec["recommended_action"] == "RMA_REPLACEMENT" and (auth != "GENUINE" or w_status != "IN_WARRANTY"):
        overrides.append("Changed RMA_REPLACEMENT to NONE: drive is not genuine and in warranty.")
        rec["recommended_action"] = "NONE"

    needs_approval = rec["recommended_action"] in APPROVAL_ACTIONS
    return {
        "recommendation": rec,
        "overrides": overrides,
        "needs_approval": needs_approval,
        "status": "PENDING_REVIEW" if needs_approval else "COMPLETED",
    }


def after_guard(state: CaseState) -> str:
    return "blocked" if state.get("blocked") else "verify"


def after_verify(state: CaseState) -> str:
    return "missing_info" if state["authenticity"]["status"] == "UNKNOWN_SERIAL" else "warranty"


def after_warranty(state: CaseState) -> str:
    asks_about_warranty = any(w in state["safe_question"].lower() for w in WARRANTY_WORDS)
    no_date = state["warranty"].get("status") == "NO_PURCHASE_DATE"
    genuine = state["authenticity"]["status"] == "GENUINE"
    return "missing_info" if (no_date and asks_about_warranty and genuine) else "policy"


def build_graph():
    g = StateGraph(CaseState)
    g.add_node("guard", guard)
    g.add_node("blocked", blocked_response)
    g.add_node("verify", verify)
    g.add_node("warranty", warranty)
    g.add_node("missing_info", missing_info)
    g.add_node("policy", policy)
    g.add_node("draft", draft)
    g.add_node("finalize", finalize)

    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", after_guard, {"blocked": "blocked", "verify": "verify"})
    g.add_conditional_edges("verify", after_verify, {"missing_info": "missing_info", "warranty": "warranty"})
    g.add_conditional_edges("warranty", after_warranty, {"missing_info": "missing_info", "policy": "policy"})
    g.add_edge("policy", "draft")
    g.add_edge("draft", "finalize")
    g.add_edge("missing_info", "finalize")
    g.add_edge("blocked", END)
    g.add_edge("finalize", END)
    return g.compile()


_GRAPH = None


def run_case(serial: str, question: str) -> dict[str, Any]:
    """Run one case end to end and return everything needed for the audit log."""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    settings = get_settings()
    final = _GRAPH.invoke({"serial": serial.strip().upper(), "question": question.strip()})
    rec = final.get("recommendation", {})
    return {
        "serial": serial.strip().upper(),
        "status": final.get("status"),
        "needs_approval": final.get("needs_approval", False),
        "recommendation": {k: v for k, v in rec.items() if k != "usage"},
        "authenticity": final.get("authenticity"),
        "warranty": final.get("warranty"),
        "sources": [{"source": p["source"], "score": p["score"]} for p in final.get("policies", [])],
        "overrides": final.get("overrides", []),
        "pii_masked": final.get("pii_masked", False),
        "trace": {
            "model_id": settings.model_id,
            "prompt_version": llm.PROMPT_VERSION,
            "knowledge_base_id": settings.knowledge_base_id,
            "guardrail": f"{settings.guardrail_id}:{settings.guardrail_version}",
            "stage": settings.stage,
            "token_usage": rec.get("usage", {}),
        },
    }

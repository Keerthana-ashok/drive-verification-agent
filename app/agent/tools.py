"""Tools the AI agent calls.

verify_authenticity and lookup_warranty are deterministic code: they decide
facts. The LLM never decides authenticity or warranty dates; it only explains
them. In a real company each of these would be its own service behind an API;
here they read fictional mock data so the project is safe to publish.
"""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

import boto3

from .config import get_settings

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "drives.json"

WARRANTY_YEARS = {"enterprise": 3, "consumer": 2}
REFURBISHED_YEARS = 1


@lru_cache(maxsize=1)
def _drives() -> dict:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)["drives"]


def _today() -> date:
    s = get_settings().as_of_date
    return date.fromisoformat(s) if s else date.today()


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February
        return d.replace(year=d.year + years, day=28)


def verify_authenticity(serial: str) -> dict:
    """Mock of the cryptographic check (X.509 chain + challenge-response)."""
    drive = _drives().get(serial)
    if drive is None:
        return {"status": "UNKNOWN_SERIAL", "serial": serial}
    genuine = drive["cert_chain_valid"] and drive["challenge_response_ok"]
    return {
        "status": "GENUINE" if genuine else "NOT_VERIFIED",
        "serial": serial,
        "cert_chain_valid": drive["cert_chain_valid"],
        "challenge_response_ok": drive["challenge_response_ok"],
    }


def lookup_warranty(serial: str) -> dict:
    """Mock warranty database lookup. Dates are computed in code, not by the LLM."""
    drive = _drives().get(serial)
    if drive is None:
        return {"status": "UNKNOWN_SERIAL"}
    years = REFURBISHED_YEARS if drive["refurbished"] else WARRANTY_YEARS[drive["segment"]]
    info = {
        "model": drive["model"],
        "sku": drive["sku"],
        "segment": drive["segment"],
        "refurbished": drive["refurbished"],
        "fips_certified": drive["fips_certified"],
        "firmware": drive["firmware"],
        "warranty_years": years,
        "purchase_date": drive["purchase_date"],
        "as_of": _today().isoformat(),
    }
    if not drive["purchase_date"]:
        info["status"] = "NO_PURCHASE_DATE"
        return info
    expires = _add_years(date.fromisoformat(drive["purchase_date"]), years)
    info["warranty_expires"] = expires.isoformat()
    info["status"] = "IN_WARRANTY" if _today() <= expires else "EXPIRED"
    return info


def search_policies(query: str, top_k: int = 4) -> list[dict]:
    """RAG retrieval: ask the Bedrock Knowledge Base for the closest policy sections."""
    settings = get_settings()
    if not settings.knowledge_base_id:
        return []
    client = boto3.client("bedrock-agent-runtime", region_name=settings.region)
    resp = client.retrieve(
        knowledgeBaseId=settings.knowledge_base_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
    )
    results = []
    for r in resp.get("retrievalResults", []):
        uri = r.get("location", {}).get("s3Location", {}).get("uri", "")
        results.append(
            {
                "text": r.get("content", {}).get("text", ""),
                "source": uri.rsplit("/", 1)[-1] if uri else "unknown",
                "score": round(float(r.get("score", 0.0)), 3),
            }
        )
    return results

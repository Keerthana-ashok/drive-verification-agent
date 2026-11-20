"""Settings read from environment variables.

Everything that might change between staging and prod (model, knowledge base,
guardrail) is configuration, never code. Switching models is a one-line change.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    region: str
    model_id: str
    knowledge_base_id: str
    guardrail_id: str
    guardrail_version: str
    prompt_version: str
    stage: str
    as_of_date: str  # optional "YYYY-MM-DD"; fixes "today" for repeatable evals


def get_settings() -> Settings:
    return Settings(
        region=os.environ.get("AWS_REGION", "us-east-1"),
        model_id=os.environ.get("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
        knowledge_base_id=os.environ.get("KNOWLEDGE_BASE_ID", ""),
        guardrail_id=os.environ.get("GUARDRAIL_ID", ""),
        guardrail_version=os.environ.get("GUARDRAIL_VERSION", "DRAFT"),
        prompt_version=os.environ.get("PROMPT_VERSION", "v1"),
        stage=os.environ.get("STAGE", "local"),
        as_of_date=os.environ.get("AS_OF_DATE", ""),
    )

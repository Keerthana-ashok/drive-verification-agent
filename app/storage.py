"""DynamoDB storage for cases and the audit log.

The audit table is insert-only: the Lambda role is granted PutItem on it and
nothing else, so no code path can edit or delete an audit record.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

_dynamo = None


def _db():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _dynamo


def _cases():
    return _db().Table(os.environ["CASES_TABLE"])


def _audit():
    return _db().Table(os.environ["AUDIT_TABLE"])


def _to_dynamo(obj):
    """DynamoDB needs Decimal instead of float."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def _from_dynamo(obj):
    return json.loads(json.dumps(obj, default=lambda d: float(d) if isinstance(d, Decimal) else str(d)))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_audit(case_id: str, event: str, actor: str, detail: dict) -> None:
    ts = now()
    _audit().put_item(
        Item=_to_dynamo(
            {"caseId": case_id, "eventKey": f"{ts}#{event}#{uuid.uuid4().hex[:6]}", "event": event,
             "actor": actor, "at": ts, "detail": detail}
        )
    )


def create_case(result: dict, created_by: str, question: str) -> dict:
    case = {
        "caseId": uuid.uuid4().hex[:12],
        "createdAt": now(),
        "createdBy": created_by,
        "question": question,
        **result,
    }
    _cases().put_item(Item=_to_dynamo(case))
    write_audit(case["caseId"], "CASE_CREATED", created_by, result)
    return case


def get_case(case_id: str) -> dict | None:
    item = _cases().get_item(Key={"caseId": case_id}).get("Item")
    return _from_dynamo(item) if item else None


def list_cases(status: str, limit: int = 50) -> list[dict]:
    resp = _cases().query(
        IndexName="status-index",
        KeyConditionExpression=Key("status").eq(status),
        ScanIndexForward=False,
        Limit=limit,
    )
    return [_from_dynamo(i) for i in resp.get("Items", [])]


def decide_case(case_id: str, decision: str, reviewer: str, reason: str) -> dict:
    new_status = "APPROVED" if decision == "APPROVE" else "REJECTED"
    resp = _cases().update_item(
        Key={"caseId": case_id},
        UpdateExpression="SET #s = :new, decidedBy = :r, decidedAt = :t, decisionReason = :why",
        ConditionExpression="#s = :pending",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":new": new_status, ":r": reviewer, ":t": now(), ":why": reason,
                                   ":pending": "PENDING_REVIEW"},
        ReturnValues="ALL_NEW",
    )
    write_audit(case_id, f"CASE_{new_status}", reviewer, {"reason": reason})
    return _from_dynamo(resp["Attributes"])

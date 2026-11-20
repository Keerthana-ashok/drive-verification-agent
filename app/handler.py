"""Lambda entry point for the HTTP API.

Routes:
  GET  /health                      no login        health check for smoke tests
  POST /cases                       any signed-in   run the AI agent on a case
  GET  /cases?status=PENDING_REVIEW reviewers       approval queue
  GET  /cases/{caseId}              creator/reviewer
  POST /cases/{caseId}/decision     reviewers       approve or reject (not your own case)
"""
from __future__ import annotations

import json
import logging
import os
import re

from botocore.exceptions import ClientError

from app import storage
from app.agent.graph import run_case

log = logging.getLogger()
log.setLevel(logging.INFO)

SERIAL_RE = re.compile(r"^[A-Za-z0-9-]{3,32}$")


def _resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


def _claims(event: dict) -> dict:
    return event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {}) or {}


def _groups(claims: dict) -> set[str]:
    raw = claims.get("cognito:groups", "")
    if isinstance(raw, list):
        return set(raw)
    return {g for g in re.split(r"[\s,\[\]]+", raw) if g}


def _user(claims: dict) -> str:
    return claims.get("email") or claims.get("cognito:username") or claims.get("sub", "unknown")


def handler(event, context):
    route = event.get("routeKey", "")
    claims = _claims(event)
    user, groups = _user(claims), _groups(claims)
    is_reviewer = "reviewers" in groups
    path_params = event.get("pathParameters") or {}

    try:
        if route == "GET /health":
            return _resp(200, {"ok": True, "stage": os.environ.get("STAGE", "local")})

        if route == "POST /cases":
            body = json.loads(event.get("body") or "{}")
            serial, question = str(body.get("serial", "")), str(body.get("question", ""))
            if not SERIAL_RE.match(serial) or not 3 <= len(question) <= 1000:
                return _resp(400, {"error": "Send a serial (3-32 letters, digits, dashes) and a question (3-1000 characters)."})
            result = run_case(serial, question)
            case = storage.create_case(result, created_by=user, question=question)
            log.info(json.dumps({"event": "case_created", "caseId": case["caseId"], "status": case["status"],
                                 "action": result["recommendation"].get("recommended_action")}))
            return _resp(201, case)

        if route == "GET /cases":
            if not is_reviewer:
                return _resp(403, {"error": "Only reviewers can see the approval queue."})
            status = (event.get("queryStringParameters") or {}).get("status", "PENDING_REVIEW")
            return _resp(200, {"cases": storage.list_cases(status)})

        if route == "GET /cases/{caseId}":
            case = storage.get_case(path_params.get("caseId", ""))
            if not case:
                return _resp(404, {"error": "Case not found."})
            if case["createdBy"] != user and not is_reviewer:
                return _resp(403, {"error": "You can only view your own cases."})
            return _resp(200, case)

        if route == "POST /cases/{caseId}/decision":
            if not is_reviewer:
                return _resp(403, {"error": "Only reviewers can approve or reject."})
            case_id = path_params.get("caseId", "")
            case = storage.get_case(case_id)
            if not case:
                return _resp(404, {"error": "Case not found."})
            if case["createdBy"] == user:
                return _resp(403, {"error": "You can't approve or reject a case you created."})
            body = json.loads(event.get("body") or "{}")
            decision, reason = body.get("decision"), str(body.get("reason", ""))[:500]
            if decision not in ("APPROVE", "REJECT") or not reason:
                return _resp(400, {"error": "Send decision APPROVE or REJECT and a reason."})
            try:
                return _resp(200, storage.decide_case(case_id, decision, user, reason))
            except ClientError as e:
                if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    return _resp(409, {"error": "This case is not waiting for review."})
                raise

        return _resp(404, {"error": f"Unknown route {route}"})
    except Exception:  # noqa: BLE001 - log full error, return a safe message
        log.exception("Unhandled error on %s", route)
        return _resp(500, {"error": "Something went wrong. The error was logged."})

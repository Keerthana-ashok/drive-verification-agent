"""Handler tests: routing, input checks and role rules, with storage faked."""
import json

import pytest

from app import handler


def event(route, body=None, groups="", email="rep@example.com", path=None, query=None):
    return {
        "routeKey": route,
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path,
        "queryStringParameters": query,
        "requestContext": {"authorizer": {"jwt": {"claims": {"email": email, "cognito:groups": groups}}}},
    }


@pytest.fixture
def fake_storage(monkeypatch):
    cases = {"c1": {"caseId": "c1", "createdBy": "rep@example.com", "status": "PENDING_REVIEW"}}
    monkeypatch.setattr(handler.storage, "get_case", lambda cid: cases.get(cid))
    monkeypatch.setattr(handler.storage, "decide_case",
                        lambda cid, d, r, why: {**cases[cid], "status": "APPROVED", "decidedBy": r})
    monkeypatch.setattr(handler.storage, "list_cases", lambda status: list(cases.values()))
    return cases


def test_health_needs_no_login():
    r = handler.handler({"routeKey": "GET /health"}, None)
    assert r["statusCode"] == 200


def test_bad_serial_rejected():
    r = handler.handler(event("POST /cases", {"serial": "!!", "question": "hello there"}), None)
    assert r["statusCode"] == 400


def test_queue_is_reviewers_only(fake_storage):
    assert handler.handler(event("GET /cases", groups="[reps]"), None)["statusCode"] == 403
    assert handler.handler(event("GET /cases", groups="[reviewers]"), None)["statusCode"] == 200


def test_reviewer_cannot_approve_own_case(fake_storage):
    r = handler.handler(event("POST /cases/{caseId}/decision", {"decision": "APPROVE", "reason": "ok"},
                              groups="[reviewers]", email="rep@example.com", path={"caseId": "c1"}), None)
    assert r["statusCode"] == 403


def test_other_reviewer_can_approve(fake_storage):
    r = handler.handler(event("POST /cases/{caseId}/decision", {"decision": "APPROVE", "reason": "checked"},
                              groups="[reviewers]", email="lead@example.com", path={"caseId": "c1"}), None)
    assert r["statusCode"] == 200
    assert json.loads(r["body"])["status"] == "APPROVED"

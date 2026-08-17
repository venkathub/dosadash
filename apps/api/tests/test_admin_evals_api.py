"""Eval scoreboard API tests: CI ingest (internal token), RBAC reads,
payload compatibility with run_live_evals.py output."""

import pytest

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_shared import EvalRunIn, Role

EVAL_RUNS = "/api/v1/admin/eval-runs"

# Shaped exactly like evals/suites/run_live_evals.py --json output
# (plus the CI-supplied git_sha/trigger provenance).
SAMPLE_RUN = {
    "ran_at": "2026-08-17T18:04:11+00:00",
    "git_sha": "ad44ae3e10c92ac462547b3224653ff7d4a49be3",
    "trigger": "ci",
    "cases": 80,
    "metrics": {
        "order_accuracy": 0.9625,
        "tool_correctness": 1.0,
        "guardrail_bypasses": 0,
        "guardrail_cases": 21,
    },
    "gates_passed": True,
    "failures": [],
    "case_reports": [
        {
            "id": "ord-035",
            "tags": ["basic"],
            "language": "tanglish",
            "accuracy_problems": ["draft missing Appam"],
            "tool_violations": [],
            "bypasses": [],
        }
    ],
}


@pytest.fixture
def internal_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_token", "internal-test-token")
    return {"X-Internal-Token": "internal-test-token"}


async def _login_as(db_session, phone: str, role: Role) -> dict:
    user = User(phone=phone, name=f"{role.value} user", role=role)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin(db_session):
    return await _login_as(db_session, "+919555556001", Role.ADMIN)


# ---------------------------------------------------------------------- ingest


async def test_ingest_requires_internal_token(client, internal_token):
    assert (await client.post(EVAL_RUNS, json=SAMPLE_RUN)).status_code == 403
    bad = {"X-Internal-Token": "wrong"}
    assert (await client.post(EVAL_RUNS, json=SAMPLE_RUN, headers=bad)).status_code == 403


async def test_ingest_503_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_token", "")
    resp = await client.post(EVAL_RUNS, json=SAMPLE_RUN, headers={"X-Internal-Token": "x"})
    assert resp.status_code == 503


async def test_ingest_promotes_metrics_to_columns(client, internal_token):
    resp = await client.post(EVAL_RUNS, json=SAMPLE_RUN, headers=internal_token)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["order_accuracy"] == 0.9625
    assert body["tool_correctness"] == 1.0
    assert body["guardrail_bypasses"] == 0
    assert body["guardrail_cases"] == 21
    assert body["tone"] is None
    assert body["gates_passed"] is True
    assert body["cases"] == 80
    assert body["git_sha"].startswith("ad44ae3")


async def test_ingest_rejects_missing_headline_metrics(client, internal_token):
    broken = {**SAMPLE_RUN, "metrics": {"tone": 0.9}}
    resp = await client.post(EVAL_RUNS, json=broken, headers=internal_token)
    assert resp.status_code == 422


async def test_failed_runs_are_recorded_too(client, internal_token):
    failed = {
        **SAMPLE_RUN,
        "gates_passed": False,
        "failures": ["order_accuracy 85.00% < 95%"],
        "metrics": {**SAMPLE_RUN["metrics"], "order_accuracy": 0.85},
    }
    resp = await client.post(EVAL_RUNS, json=failed, headers=internal_token)
    assert resp.status_code == 201
    assert resp.json()["gates_passed"] is False
    assert resp.json()["failures"] == ["order_accuracy 85.00% < 95%"]


# ----------------------------------------------------------------------- reads


async def test_list_requires_admin(client, db_session):
    assert (await client.get(EVAL_RUNS)).status_code == 401
    kitchen = await _login_as(db_session, "+919555556002", Role.KITCHEN_STAFF)
    assert (await client.get(EVAL_RUNS, headers=kitchen)).status_code == 403


async def test_scoreboard_list_and_detail(client, internal_token, admin):
    first = {**SAMPLE_RUN, "ran_at": "2026-08-16T10:00:00+00:00", "git_sha": "aaa111"}
    await client.post(EVAL_RUNS, json=first, headers=internal_token)
    await client.post(EVAL_RUNS, json=SAMPLE_RUN, headers=internal_token)

    listing = (await client.get(EVAL_RUNS, headers=admin)).json()
    assert len(listing) == 2
    assert listing[0]["git_sha"].startswith("ad44ae3")  # newest first
    assert "case_reports" not in listing[0]  # list stays light

    detail = (await client.get(f"{EVAL_RUNS}/{listing[0]['id']}", headers=admin)).json()
    assert detail["case_reports"][0]["id"] == "ord-035"
    assert detail["case_reports"][0]["accuracy_problems"] == ["draft missing Appam"]

    assert (await client.get(f"{EVAL_RUNS}/999999", headers=admin)).status_code == 404


# ------------------------------------------------------- payload compatibility


def test_run_live_evals_payload_shape_validates():
    """The suite's JSON payload must always parse as EvalRunIn — if
    run_live_evals.py changes shape, this breaks before CI ingest does."""
    parsed = EvalRunIn.model_validate(SAMPLE_RUN)
    assert parsed.metrics["order_accuracy"] == 0.9625
    assert parsed.case_reports[0].id == "ord-035"

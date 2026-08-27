"""GitHub → api webhook (Phase 14 slice 1): HMAC auth, repo pinning,
delivery idempotency, and the event→stage→status mapping that finally
syncs the loop's tail (fixer/PR/merge/verify) back into DosaDash."""

import hashlib
import hmac
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import config
from dosadash_api.db.models import FeedbackEvent, FeedbackReport

WEBHOOK = "/api/v1/github/webhook"
SECRET = "test-github-webhook-secret"
REPO = "venkathub/dosadash"


@pytest.fixture(autouse=True)
def _secret_env(monkeypatch):
    monkeypatch.setenv("API_GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("API_GITHUB_REPO", REPO)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


async def _post(
    client,
    event: str,
    payload: dict,
    *,
    delivery: str = "d-0001",
    signature: str | None = None,
):
    body = json.dumps(payload).encode()
    return await client.post(
        WEBHOOK,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature if signature is not None else _sign(body),
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
        },
    )


def _report(**overrides) -> FeedbackReport:
    base = dict(
        reporter_tier="ANON",
        type="BUG",
        status="TRACKED",
        title="Cart total wrong",
        description="GST negative",
        dedupe_hash="a" * 64,
        github_issue_number=120,
    )
    base.update(overrides)
    return FeedbackReport(**base)


def _issues_payload(action: str, *, label: str | None = None, **issue_extra) -> dict:
    payload = {
        "action": action,
        "repository": {"full_name": REPO},
        "issue": {"number": 120, **issue_extra},
    }
    if label is not None:
        payload["label"] = {"name": label}
    return payload


async def _events(db_session: AsyncSession, report_id: int) -> list[FeedbackEvent]:
    rows = await db_session.execute(
        select(FeedbackEvent).where(FeedbackEvent.report_id == report_id).order_by(FeedbackEvent.id)
    )
    return list(rows.scalars())


# ---------------------------------------------------------------- auth


async def test_unconfigured_503(client, monkeypatch) -> None:
    monkeypatch.delenv("API_GITHUB_WEBHOOK_SECRET")
    config.get_settings.cache_clear()
    resp = await _post(client, "ping", {"zen": "hi"})
    assert resp.status_code == 503


async def test_bad_signature_403(client) -> None:
    resp = await _post(client, "ping", {"zen": "hi"}, signature="sha256=" + "0" * 64)
    assert resp.status_code == 403


async def test_ping_ok(client) -> None:
    resp = await _post(client, "ping", {"zen": "hi"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "event": "ping"}


async def test_foreign_repo_ignored(client, db_session: AsyncSession) -> None:
    db_session.add(_report())
    await db_session.commit()
    payload = _issues_payload("labeled", label="ai:fixed")
    payload["repository"]["full_name"] = "someone/fork"
    resp = await _post(client, "issues", payload)
    assert resp.status_code == 200
    assert resp.json()["ignored"] == "repo"


# ------------------------------------------------------------- label flow


async def test_trigger_label_starts_fix(client, db_session: AsyncSession) -> None:
    report = _report(status="APPROVED")
    db_session.add(report)
    await db_session.commit()
    resp = await _post(client, "issues", _issues_payload("labeled", label="ai:approved"))
    assert resp.status_code == 200
    assert resp.json()["stage"] == "FIX_STARTED"
    await db_session.refresh(report)
    assert report.status == "FIXING"
    events = await _events(db_session, report.id)
    assert [e.stage for e in events] == ["FIX_STARTED"]
    assert events[0].actor == "webhook:github"
    assert events[0].delivery_id == "d-0001"


async def test_fixed_label_projects_fixed(client, db_session: AsyncSession) -> None:
    report = _report(status="FIXING")
    db_session.add(report)
    await db_session.commit()
    resp = await _post(client, "issues", _issues_payload("labeled", label="ai:fixed"))
    assert resp.json()["stage"] == "FIXED"
    await db_session.refresh(report)
    assert report.status == "FIXED"


async def test_verified_label_stamps_verified_at(client, db_session: AsyncSession) -> None:
    report = _report(status="FIXED")
    db_session.add(report)
    await db_session.commit()
    resp = await _post(client, "issues", _issues_payload("labeled", label="ai:verified"))
    assert resp.json()["stage"] == "VERIFIED"
    await db_session.refresh(report)
    assert report.status == "VERIFIED"
    assert report.verified_at is not None


async def test_needs_approval_label_escalates_only_midflight(
    client, db_session: AsyncSession
) -> None:
    # Mid-run: the fixer hit a hard limit → ESCALATED.
    report = _report(status="FIXING")
    db_session.add(report)
    await db_session.commit()
    resp = await _post(client, "issues", _issues_payload("labeled", label="ai:needs-approval"))
    assert resp.json()["stage"] == "ESCALATED"
    await db_session.refresh(report)
    assert report.status == "NEEDS_APPROVAL"

    # Self-echo: triage's own label application must NOT re-record.
    resp = await _post(
        client,
        "issues",
        _issues_payload("labeled", label="ai:needs-approval"),
        delivery="d-0002",
    )
    assert resp.json()["ignored"] == "unmapped"
    assert len(await _events(db_session, report.id)) == 1


async def test_reopened_issue(client, db_session: AsyncSession) -> None:
    report = _report(status="FIXED")
    db_session.add(report)
    await db_session.commit()
    resp = await _post(client, "issues", _issues_payload("reopened"))
    assert resp.json()["stage"] == "REOPENED"
    await db_session.refresh(report)
    assert report.status == "REOPENED"


async def test_closed_is_timeline_only(client, db_session: AsyncSession) -> None:
    report = _report(status="VERIFIED")
    db_session.add(report)
    await db_session.commit()
    resp = await _post(client, "issues", _issues_payload("closed", state_reason="completed"))
    assert resp.json()["stage"] == "CLOSED"
    await db_session.refresh(report)
    assert report.status == "VERIFIED"  # projection untouched


# ------------------------------------------------------------- comments


async def test_rca_comment_recorded_and_redacted(client, db_session: AsyncSession) -> None:
    report = _report(status="FIXING")
    db_session.add(report)
    await db_session.commit()
    payload = _issues_payload("created")
    payload["comment"] = {
        "body": "## Root cause analysis\nHydration mismatch. Reporter said call +919876543210."
    }
    resp = await _post(client, "issue_comment", payload)
    assert resp.json()["stage"] == "RCA_POSTED"
    events = await _events(db_session, report.id)
    excerpt = events[0].payload["excerpt"]
    assert "Hydration mismatch" in excerpt
    assert "+919876543210" not in excerpt  # Rule 8, defensively
    await db_session.refresh(report)
    assert report.status == "FIXING"  # timeline-only


async def test_ordinary_comment_ignored(client, db_session: AsyncSession) -> None:
    report = _report()
    db_session.add(report)
    await db_session.commit()
    payload = _issues_payload("created")
    payload["comment"] = {"body": "Nice work!"}
    resp = await _post(client, "issue_comment", payload)
    assert resp.json()["ignored"] == "unmapped"


async def test_verification_comment_recorded(client, db_session: AsyncSession) -> None:
    report = _report(status="FIXED")
    db_session.add(report)
    await db_session.commit()
    payload = _issues_payload("created")
    payload["comment"] = {"body": "## Prod verification\nProbed live.\nVERDICT: verified"}
    resp = await _post(client, "issue_comment", payload)
    assert resp.json()["stage"] == "VERIFICATION_POSTED"


# ------------------------------------------------------------ pull requests


def _pr_payload(action: str, *, merged: bool = False, head_ref: str = "fix/issue-120") -> dict:
    return {
        "action": action,
        "repository": {"full_name": REPO},
        "pull_request": {
            "number": 7,
            "merged": merged,
            "head": {"ref": head_ref},
            "body": "Fixes #120",
            "html_url": f"https://github.com/{REPO}/pull/7",
        },
    }


async def test_pr_opened_links_via_branch(client, db_session: AsyncSession) -> None:
    report = _report(status="FIXING")
    db_session.add(report)
    await db_session.commit()
    resp = await _post(client, "pull_request", _pr_payload("opened"))
    assert resp.json()["stage"] == "PR_OPENED"
    await db_session.refresh(report)
    assert report.status == "PR_OPEN"
    assert report.fix_pr_number == 7


async def test_pr_merged_projects_fixed(client, db_session: AsyncSession) -> None:
    report = _report(status="PR_OPEN", fix_pr_number=7)
    db_session.add(report)
    await db_session.commit()
    resp = await _post(client, "pull_request", _pr_payload("closed", merged=True))
    assert resp.json()["stage"] == "PR_MERGED"
    await db_session.refresh(report)
    assert report.status == "FIXED"


async def test_pr_links_via_body_when_branch_foreign(client, db_session: AsyncSession) -> None:
    report = _report(status="FIXING")
    db_session.add(report)
    await db_session.commit()
    resp = await _post(client, "pull_request", _pr_payload("opened", head_ref="feature/whatever"))
    assert resp.json()["stage"] == "PR_OPENED"  # fell back to "Fixes #120"


# ------------------------------------------------------------- idempotency


async def test_duplicate_delivery_noops(client, db_session: AsyncSession) -> None:
    report = _report(status="APPROVED")
    db_session.add(report)
    await db_session.commit()
    payload = _issues_payload("labeled", label="ai:approved")
    first = await _post(client, "issues", payload, delivery="d-42")
    assert first.json()["stage"] == "FIX_STARTED"
    second = await _post(client, "issues", payload, delivery="d-42")
    assert second.json()["ignored"] == "duplicate-delivery"
    assert len(await _events(db_session, report.id)) == 1


async def test_unknown_issue_ignored(client, db_session: AsyncSession) -> None:
    resp = await _post(client, "issues", _issues_payload("labeled", label="ai:fixed"))
    assert resp.json()["ignored"] == "unmapped"


async def test_spec_comment_recorded_timeline_only(client, db_session: AsyncSession) -> None:
    """Phase 15 S2: the spec agent's '## Spec' comment lands as a
    SPEC_POSTED timeline event; the report STAYS NEEDS_APPROVAL — the
    human decides WITH the spec, the spec never decides."""
    report = _report(status="NEEDS_APPROVAL")
    db_session.add(report)
    await db_session.commit()
    payload = _issues_payload("created")
    payload["comment"] = {
        "body": "## Spec\n**Problem** dark mode request.\n**Decomposition** two S steps."
    }
    resp = await _post(client, "issue_comment", payload)
    assert resp.json()["stage"] == "SPEC_POSTED"
    events = await _events(db_session, report.id)
    assert "dark mode" in events[0].payload["excerpt"]
    await db_session.refresh(report)
    assert report.status == "NEEDS_APPROVAL"  # timeline-only, projection untouched

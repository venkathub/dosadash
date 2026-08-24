"""Feedback intake (Phase 13 slice 1): storage, redaction, dedupe, GitHub
mirror degrade, reporter tiers, and the untrusted-fence issue body."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackReport, User
from dosadash_api.main import app
from dosadash_api.services import feedback_service
from dosadash_api.services.github_client import GitHubError, get_github_client
from dosadash_shared import (
    LABEL_BUG,
    LABEL_FEATURE,
    LABEL_USER_REPORTED,
    UNTRUSTED_BEGIN,
    UNTRUSTED_END,
    Role,
)


class FakeGitHub:
    """Records calls; issue numbers count up from 101."""

    def __init__(self, *, enabled: bool = True, fail: bool = False) -> None:
        self._enabled = enabled
        self._fail = fail
        self.created: list[dict] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def create_issue(self, *, title: str, body: str, labels: list[str]) -> int:
        if self._fail:
            raise GitHubError("GitHub call failed: boom")
        self.created.append({"title": title, "body": body, "labels": labels})
        return 100 + len(self.created)


@pytest.fixture
def fake_github():
    fake = FakeGitHub()
    app.dependency_overrides[get_github_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_github_client, None)


async def _login(client, phone: str) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


def _payload(**overrides) -> dict:
    base = {
        "type": "BUG",
        "title": "Cart total wrong after coupon",
        "description": "Applied WELCOME10 and the GST line went negative on checkout.",
        "context": {"route": "/checkout"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- intake


async def test_anonymous_bug_tracked(client, db_session: AsyncSession, fake_github) -> None:
    resp = await client.post("/api/v1/feedback", json=_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "TRACKED"
    assert body["github_issue_number"] == 101
    assert body["duplicate"] is False
    # customer wire shape hides backoffice fields
    assert "reporter_tier" not in body and "dedupe_hash" not in body

    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert row.reporter_tier == "ANON" and row.user_id is None

    issue = fake_github.created[0]
    assert issue["title"].startswith("[user-bug] ")
    assert set(issue["labels"]) == {LABEL_USER_REPORTED, LABEL_BUG}
    assert UNTRUSTED_BEGIN in issue["body"] and UNTRUSTED_END in issue["body"]
    # user text lands AFTER the fence opens (metadata table is ours)
    assert issue["body"].index(UNTRUSTED_BEGIN) < issue["body"].index("WELCOME10")


async def test_phone_redacted_before_storage_and_github(
    client, db_session: AsyncSession, fake_github
) -> None:
    resp = await client.post(
        "/api/v1/feedback",
        json=_payload(description="OTP never arrives, call me back on +91 98765 43210 please."),
    )
    assert resp.status_code == 201
    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert "98765" not in row.description and "[phone]" in row.description
    assert "98765" not in fake_github.created[0]["body"]


async def test_duplicate_collapses_onto_open_report(client, fake_github) -> None:
    first = await client.post("/api/v1/feedback", json=_payload())
    assert first.status_code == 201
    # trivially different casing/whitespace must still collapse
    dup = await client.post(
        "/api/v1/feedback", json=_payload(title="cart TOTAL wrong  after coupon")
    )
    assert dup.status_code == 200
    assert dup.json()["duplicate"] is True
    assert dup.json()["id"] == first.json()["id"]
    assert len(fake_github.created) == 1  # no second issue


async def test_github_failure_degrades_to_store_only(client, db_session: AsyncSession) -> None:
    app.dependency_overrides[get_github_client] = lambda: FakeGitHub(fail=True)
    try:
        resp = await client.post("/api/v1/feedback", json=_payload())
    finally:
        app.dependency_overrides.pop(get_github_client, None)
    assert resp.status_code == 201  # reporter never sees the outage
    assert resp.json()["status"] == "RECEIVED"
    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert row.github_error and "boom" in row.github_error
    assert row.github_issue_number is None


async def test_github_disabled_stores_locally(client, db_session: AsyncSession) -> None:
    app.dependency_overrides[get_github_client] = lambda: FakeGitHub(enabled=False)
    try:
        resp = await client.post("/api/v1/feedback", json=_payload())
    finally:
        app.dependency_overrides.pop(get_github_client, None)
    assert resp.status_code == 201
    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert row.status == "RECEIVED" and "disabled" in (row.github_error or "")


# ---------------------------------------------------------------- tiers


async def test_customer_tier_and_user_link(client, db_session: AsyncSession, fake_github) -> None:
    headers = await _login(client, "9111177801")
    resp = await client.post(
        "/api/v1/feedback",
        json=_payload(type="FEATURE", title="Please add jain filter to search"),
        headers=headers,
    )
    assert resp.status_code == 201
    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert row.reporter_tier == "CUSTOMER" and row.user_id is not None
    assert LABEL_FEATURE in fake_github.created[0]["labels"]
    assert fake_github.created[0]["title"].startswith("[user-feature] ")


async def test_staff_tier(client, db_session: AsyncSession, fake_github) -> None:
    headers = await _login(client, "9111177802")
    user = (
        await db_session.execute(select(User).where(User.phone.contains("9111177802")))
    ).scalar_one()
    user.role = Role.KITCHEN_STAFF
    await db_session.commit()
    resp = await client.post(
        "/api/v1/feedback", json=_payload(title="KDS badge overlaps timer"), headers=headers
    )
    assert resp.status_code == 201
    row = (await db_session.execute(select(FeedbackReport))).scalar_one()
    assert row.reporter_tier == "STAFF"


async def test_invalid_token_is_401_never_silently_anonymous(client, fake_github) -> None:
    resp = await client.post(
        "/api/v1/feedback", json=_payload(), headers={"Authorization": "Bearer not-a-token"}
    )
    assert resp.status_code == 401
    assert fake_github.created == []


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "bad",
    [
        {"title": "x"},  # too short
        {"description": "short"},  # too short
        {"type": "RANT"},  # not a FeedbackType
    ],
)
async def test_validation_rejects(client, fake_github, bad: dict) -> None:
    resp = await client.post("/api/v1/feedback", json=_payload(**bad))
    assert resp.status_code == 422
    assert fake_github.created == []


# ---------------------------------------------------------------- service units


def test_dedupe_hash_normalizes_case_and_whitespace() -> None:
    a = feedback_service.compute_dedupe_hash("BUG", "Cart  broken", "GST negative")
    b = feedback_service.compute_dedupe_hash("BUG", "cart broken", "gst NEGATIVE")
    c = feedback_service.compute_dedupe_hash("FEATURE", "Cart broken", "GST negative")
    assert a == b
    assert a != c  # type participates — same words, different intent

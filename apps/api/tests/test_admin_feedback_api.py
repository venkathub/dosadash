"""Admin feedback inbox (Phase 13 slice 2): RBAC, filters, github_repo hint."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import FeedbackReport, User
from dosadash_shared import Role


async def _login(client, phone: str) -> dict:
    req = await client.post("/api/v1/auth/otp/request", json={"phone": phone})
    otp = req.json()["demo_otp"]
    verify = await client.post("/api/v1/auth/otp/verify", json={"phone": phone, "otp": otp})
    return {"Authorization": f"Bearer {verify.json()['access_token']}"}


async def _admin(client, db_session: AsyncSession, phone="9111177901") -> dict:
    headers = await _login(client, phone)
    user = (await db_session.execute(select(User).where(User.phone.contains(phone)))).scalar_one()
    user.role = Role.ADMIN
    await db_session.commit()
    return headers


def _report(**overrides) -> FeedbackReport:
    base = dict(
        reporter_tier="ANON",
        type="BUG",
        status="TRACKED",
        title="Cart total wrong",
        description="GST negative",
        dedupe_hash="a" * 64,
        github_issue_number=101,
    )
    base.update(overrides)
    return FeedbackReport(**base)


async def test_requires_admin(client) -> None:
    headers = await _login(client, "9111177902")
    resp = await client.get("/api/v1/admin/feedback", headers=headers)
    assert resp.status_code == 403


async def test_list_with_filters(client, db_session: AsyncSession) -> None:
    db_session.add_all(
        [
            _report(),
            _report(
                type="FEATURE",
                status="NEEDS_APPROVAL",
                title="Jain filter",
                dedupe_hash="b" * 64,
                github_issue_number=102,
            ),
            _report(status="RECEIVED", dedupe_hash="c" * 64, github_issue_number=None),
        ]
    )
    await db_session.commit()
    headers = await _admin(client, db_session)

    resp = await client.get("/api/v1/admin/feedback", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["reports"]) == 3
    assert "github_repo" in body  # "" when integration disabled
    # newest first + full backoffice shape
    assert body["reports"][0]["dedupe_hash"] == "c" * 64
    assert body["reports"][0]["reporter_tier"] == "ANON"

    by_status = await client.get("/api/v1/admin/feedback?status=NEEDS_APPROVAL", headers=headers)
    assert by_status.json()["total"] == 1
    assert by_status.json()["reports"][0]["title"] == "Jain filter"

    by_type = await client.get("/api/v1/admin/feedback?type=BUG", headers=headers)
    assert by_type.json()["total"] == 2

    bad = await client.get("/api/v1/admin/feedback?status=NOPE", headers=headers)
    assert bad.status_code == 422

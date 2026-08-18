"""Admin CRM endpoint: summary, win-back ordering, empty state."""

from datetime import UTC, datetime

import pytest

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import CustomerSegment, User
from dosadash_shared import Role

CRM = "/api/v1/admin/crm/segments"


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
    return await _login_as(db_session, "+919555559001", Role.ADMIN)


async def test_empty_state(client, admin):
    resp = await client.get(CRM, headers=admin)
    assert resp.status_code == 200
    body = resp.json()
    assert body["computed_at"] is None
    assert body["tiers"] == [] and body["at_risk"] == []


async def test_summary_and_winback_ordering(client, admin, db_session):
    now = datetime.now(UTC)
    users = []
    for i, (tier, churn, ltv) in enumerate(
        [
            ("CHAMPION", 0.05, 30000.0),
            ("AT_RISK", 0.7, 9000.0),  # winback score 6300 — top target
            ("AT_RISK", 0.8, 2000.0),  # 1600
            ("LOST", 0.95, 300.0),  # 285
            ("REGULAR", 0.3, 4000.0),
        ]
    ):
        user = User(phone=f"+91955556{i:04d}", name=f"U{i}", role=Role.CUSTOMER)
        db_session.add(user)
        await db_session.flush()
        db_session.add(
            CustomerSegment(
                user_id=user.id, rfm_tier=tier, churn_risk=churn, ltv=ltv, computed_at=now
            )
        )
        users.append(user)
    await db_session.commit()

    resp = await client.get(CRM, headers=admin)
    assert resp.status_code == 200
    body = resp.json()
    assert body["computed_at"] is not None
    tiers = {t["tier"]: t for t in body["tiers"]}
    assert tiers["AT_RISK"]["users"] == 2
    assert tiers["AT_RISK"]["total_ltv"] == pytest.approx(11000.0)
    # Win-back list: AT_RISK/LOST only (REGULAR at 0.3 churn excluded),
    # ordered by ltv × churn.
    phones = [r["phone"] for r in body["at_risk"]]
    assert phones == [users[1].phone, users[2].phone, users[3].phone]

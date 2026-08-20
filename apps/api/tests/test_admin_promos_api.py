"""Admin promo suggestion endpoint tests — AI client faked (no network).

The api must re-validate everything it persists: unknown item ids, price
above parts, duplicate codes and guardrail violations are SKIPPED with a
reason, never stored. Whatever survives lands as DRAFT/inactive."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from dosadash_api.auth.security import create_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import Combo, Coupon, User
from dosadash_api.services.ai_client import AIServiceError, get_ai_client
from dosadash_shared import (
    CouponType,
    PromoComboSuggestion,
    PromoCouponSuggestion,
    PromoStats,
    PromoSuggestResult,
    Role,
)


async def _admin(db_session, phone="+919333340081") -> dict:
    user = User(phone=phone, name="Promo Admin", role=Role.ADMIN)
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        user_id=user.id, role=user.role, secret=get_settings().jwt_secret, ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


def _combo(item_ids, names, price="165.00", name="Kaapi Combo") -> PromoComboSuggestion:
    return PromoComboSuggestion(
        item_ids=item_ids,
        names=names,
        name=name,
        price=Decimal(price),
        parts_total=Decimal("180.00"),
        times_ordered=41,
        rationale="ordered together 41×",
    )


def _coupon(code="TUESDAYTREAT") -> PromoCouponSuggestion:
    return PromoCouponSuggestion(
        code=code,
        type=CouponType.PCT,
        value=Decimal("15"),
        max_discount=Decimal("75.00"),
        min_subtotal=None,
        description="15% off Tuesdays",
        rationale="slowest day",
    )


class FakePromoAI:
    def __init__(self, result: PromoSuggestResult) -> None:
        self.result = result
        self.calls = 0

    async def suggest_promos(self, *, admin_user_id: int):
        self.calls += 1
        return self.result


def _override(client, result):
    from dosadash_api.main import app

    fake = FakePromoAI(result)
    app.dependency_overrides[get_ai_client] = lambda: fake
    return fake


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    from dosadash_api.main import app

    app.dependency_overrides.pop(get_ai_client, None)


async def _menu_pair(client) -> tuple[dict, dict]:
    menu = {i["name"]: i for i in (await client.get("/api/v1/menu")).json()}
    return menu["Masala Dosa"], menu["Filter Coffee"]


async def test_suggest_persists_drafts(client, db_session):
    admin = await _admin(db_session)
    dosa, coffee = await _menu_pair(client)
    result = PromoSuggestResult(
        combos=[_combo([dosa["id"], coffee["id"]], [dosa["name"], coffee["name"]])],
        coupons=[_coupon()],
        stats=PromoStats(slow_day="Tuesday", median_aov=Decimal("310"), existing_codes=[]),
        model="gpt-4o-mini",
    )
    _override(client, result)
    resp = await client.post("/api/v1/admin/promos/suggest", headers=admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["skipped"] == []
    assert body["combos"][0]["status"] == "DRAFT"
    assert body["combos"][0]["source"] == "AI_SUGGESTED"
    assert body["coupons"][0]["is_active"] is False
    assert body["coupons"][0]["source"] == "AI_SUGGESTED"

    combo = await db_session.scalar(select(Combo).where(Combo.name == "Kaapi Combo"))
    assert combo is not None and combo.status == "DRAFT"
    coupon = await db_session.scalar(select(Coupon).where(Coupon.code == "TUESDAYTREAT"))
    assert coupon is not None and coupon.is_active is False


async def test_suggest_skips_invalid_never_persists(client, db_session):
    admin = await _admin(db_session, phone="+919333340082")
    dosa, coffee = await _menu_pair(client)
    db_session.add(
        Coupon(
            code="TAKEN",
            type=CouponType.PCT,
            value=Decimal("10"),
            max_discount=Decimal("40"),
            is_active=True,
        )
    )
    await db_session.commit()
    result = PromoSuggestResult(
        combos=[
            _combo([999999, dosa["id"]], ["Ghost", dosa["name"]], name="Ghost Combo"),
            _combo(
                [dosa["id"], coffee["id"]],
                [dosa["name"], coffee["name"]],
                price="999.00",
                name="Overpriced",
            ),
        ],
        coupons=[_coupon(code="TAKEN")],
        stats=None,
        model="gpt-4o-mini",
    )
    _override(client, result)
    resp = await client.post("/api/v1/admin/promos/suggest", headers=admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["combos"] == [] and body["coupons"] == []
    assert len(body["skipped"]) == 3
    assert await db_session.scalar(select(Combo).where(Combo.name == "Ghost Combo")) is None


async def test_suggest_requires_admin(client):
    resp = await client.post("/api/v1/admin/promos/suggest")
    assert resp.status_code in (401, 403)


async def test_suggest_ai_down_502(client, db_session):
    admin = await _admin(db_session, phone="+919333340083")

    class DownAI:
        async def suggest_promos(self, *, admin_user_id: int):
            raise AIServiceError("down")

    from dosadash_api.main import app

    app.dependency_overrides[get_ai_client] = lambda: DownAI()
    resp = await client.post("/api/v1/admin/promos/suggest", headers=admin)
    assert resp.status_code == 502

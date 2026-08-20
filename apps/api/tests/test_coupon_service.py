"""Coupon engine tests: validation ladder, discount math, checkout flow."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from dosadash_api.db.models import Coupon
from dosadash_api.services import coupon_service
from dosadash_shared import CouponType

# ------------------------------------------------------- pure discount math


def _coupon(**overrides) -> Coupon:
    defaults = dict(
        code="TEST10",
        type=CouponType.PCT,
        value=Decimal("10"),
        max_discount=Decimal("50"),
        is_active=True,
    )
    defaults.update(overrides)
    return Coupon(**defaults)


def test_pct_discount_with_cap():
    coupon = _coupon()
    assert coupon_service.compute_discount(coupon, Decimal("300")) == Decimal("30.00")
    assert coupon_service.compute_discount(coupon, Decimal("900")) == Decimal("50.00")  # capped


def test_flat_discount_never_exceeds_subtotal():
    coupon = _coupon(type=CouponType.FLAT, value=Decimal("100"), max_discount=None)
    assert coupon_service.compute_discount(coupon, Decimal("250")) == Decimal("100.00")
    assert coupon_service.compute_discount(coupon, Decimal("60")) == Decimal("60.00")


def test_discounted_gst_is_pro_rata():
    # ₹300 cart, ₹15 full GST (5%), ₹30 off → GST on ₹270 = ₹13.50
    assert coupon_service.discounted_gst(
        Decimal("15.00"), Decimal("300"), Decimal("30")
    ) == Decimal("13.50")
    # no discount → untouched
    assert coupon_service.discounted_gst(Decimal("15.00"), Decimal("300"), Decimal("0")) == Decimal(
        "15.00"
    )


# ------------------------------------------------- validation ladder (DB)


async def _make(db_session, **overrides) -> Coupon:
    coupon = _coupon(**overrides)
    db_session.add(coupon)
    await db_session.commit()
    return coupon


async def _resolve(db_session, code="TEST10", user_id=1, subtotal=Decimal("300")):
    return await coupon_service.resolve(db_session, code=code, user_id=user_id, subtotal=subtotal)


async def test_resolve_happy_path_case_insensitive(db_session):
    await _make(db_session)
    coupon, discount = await _resolve(db_session, code="test10 ")
    assert coupon.code == "TEST10"
    assert discount == Decimal("30.00")


async def test_inactive_and_unknown_rejected(db_session):
    await _make(db_session, is_active=False)
    with pytest.raises(coupon_service.CouponError, match="Invalid"):
        await _resolve(db_session)
    with pytest.raises(coupon_service.CouponError, match="Invalid"):
        await _resolve(db_session, code="NOPE")


async def test_date_window(db_session):
    now = datetime.now(UTC)
    await _make(db_session, code="EARLY", valid_from=now + timedelta(days=1))
    await _make(db_session, code="LATE", valid_to=now - timedelta(days=1))
    with pytest.raises(coupon_service.CouponError, match="not active yet"):
        await _resolve(db_session, code="EARLY")
    with pytest.raises(coupon_service.CouponError, match="expired"):
        await _resolve(db_session, code="LATE")


async def test_min_subtotal_message_tells_the_gap(db_session):
    await _make(db_session, min_subtotal=Decimal("500"))
    with pytest.raises(coupon_service.CouponError, match="Add ₹200 more"):
        await _resolve(db_session, subtotal=Decimal("300"))


async def test_usage_limits(db_session):
    from sqlalchemy import select

    from dosadash_api.db.models import Brand, Order, User
    from dosadash_shared import ChannelType, OrderState, Role

    # real FK targets: two users + one order row for the redemption
    user_a = User(phone="+919555571001", name="Coupon A", role=Role.CUSTOMER)
    user_b = User(phone="+919555571002", name="Coupon B", role=Role.CUSTOMER)
    db_session.add_all([user_a, user_b])
    await db_session.flush()
    brand_id = await db_session.scalar(select(Brand.id).limit(1))
    order = Order(
        user_id=user_a.id,
        brand_id=brand_id,
        channel=ChannelType.WEB,
        status=OrderState.PLACED,
        subtotal=Decimal("300"),
        gst=Decimal("15"),
        total=Decimal("315"),
    )
    db_session.add(order)
    await db_session.flush()

    coupon = await _make(db_session, usage_limit=1, per_user_limit=1)
    coupon_service.redeem(db_session, coupon=coupon, user_id=user_a.id, order_id=order.id)
    await db_session.commit()
    # global limit hit (one redemption exists)
    with pytest.raises(coupon_service.CouponError, match="fully redeemed"):
        await _resolve(db_session, user_id=user_b.id)
    # per-user message when only the user limit binds
    coupon.usage_limit = 10
    await db_session.commit()
    with pytest.raises(coupon_service.CouponError, match="already used"):
        await _resolve(db_session, user_id=user_a.id)
    # other users still fine
    resolved, _ = await _resolve(db_session, user_id=user_b.id)
    assert resolved.id == coupon.id

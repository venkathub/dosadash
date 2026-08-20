"""Coupon engine (Phase 7) — all discount math server-side, never client.

Validation ladder (first failure wins, with a customer-readable message):
active → date window → min subtotal → global usage limit → per-user limit.
Discounts: FLAT = value; PCT = subtotal×value/100 capped by max_discount.
A discount can never exceed the subtotal. GST is charged on the DISCOUNTED
amount (pro-rata across the per-line rates the order already computed).

Usage counting = counting redemption rows (one per order, unique
(coupon_id, order_id)) — no mutable counter to drift.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import Coupon, CouponRedemption
from dosadash_shared import CouponType


class CouponError(Exception):
    """Customer-facing rejection — .args[0] is safe to show verbatim."""


async def resolve(
    session: AsyncSession, *, code: str, user_id: int, subtotal: Decimal
) -> tuple[Coupon, Decimal]:
    """Validate `code` for this user+cart → (coupon, discount). Raises
    CouponError with a human message on any failure."""
    coupon = await session.scalar(select(Coupon).where(Coupon.code == code.strip().upper()))
    if coupon is None or not coupon.is_active:
        raise CouponError("Invalid coupon code")

    now = datetime.now(UTC)
    if coupon.valid_from is not None and now < coupon.valid_from:
        raise CouponError("This coupon is not active yet")
    if coupon.valid_to is not None and now > coupon.valid_to:
        raise CouponError("This coupon has expired")

    if coupon.min_subtotal is not None and subtotal < coupon.min_subtotal:
        short = coupon.min_subtotal - subtotal
        raise CouponError(f"Add ₹{short:.0f} more to use {coupon.code}")

    if coupon.usage_limit is not None:
        used = await session.scalar(
            select(func.count()).where(CouponRedemption.coupon_id == coupon.id)
        )
        if (used or 0) >= coupon.usage_limit:
            raise CouponError("This coupon has been fully redeemed")

    if coupon.per_user_limit is not None:
        mine = await session.scalar(
            select(func.count()).where(
                CouponRedemption.coupon_id == coupon.id,
                CouponRedemption.user_id == user_id,
            )
        )
        if (mine or 0) >= coupon.per_user_limit:
            raise CouponError("You've already used this coupon")

    return coupon, compute_discount(coupon, subtotal)


def compute_discount(coupon: Coupon, subtotal: Decimal) -> Decimal:
    if coupon.type == CouponType.FLAT:
        discount = coupon.value
    else:  # PCT
        discount = subtotal * coupon.value / 100
        if coupon.max_discount is not None:
            discount = min(discount, coupon.max_discount)
    return min(discount, subtotal).quantize(Decimal("0.01"))


def discounted_gst(gst_full: Decimal, subtotal: Decimal, discount: Decimal) -> Decimal:
    """GST on the discounted amount, pro-rata over the full-price GST (keeps
    the per-line rate mix without recomputing every line)."""
    if subtotal <= 0 or discount <= 0:
        return gst_full
    ratio = (subtotal - discount) / subtotal
    return (gst_full * ratio).quantize(Decimal("0.01"))


def redeem(session: AsyncSession, *, coupon: Coupon, user_id: int, order_id: int) -> None:
    """Record the redemption (call after the order row is flushed)."""
    session.add(CouponRedemption(coupon_id=coupon.id, user_id=user_id, order_id=order_id))

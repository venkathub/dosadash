"""Admin coupon management (Phase 7 coupon engine): CRUD + activation.

Coupons are born INACTIVE and only price carts once activated — Phase 7's
AI promo suggestions land as source=AI_SUGGESTED inactive drafts in this
same flow (the owner-approval story mirrors combos). Server-side value
guardrails apply to everyone: PCT ≤ 50%, FLAT ≤ ₹300, and a FLAT coupon
must demand a min_subtotal ≥ 2× its value (no free-food coupons).
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import Coupon, CouponRedemption, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_shared import (
    MAX_FLAT_VALUE,
    MAX_PCT_VALUE,
    CouponCreateIn,
    CouponOut,
    CouponType,
    CouponUpdateIn,
    Role,
)

router = APIRouter(prefix="/api/v1/admin/coupons", tags=["admin:coupons"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)


def validate_coupon_values(
    coupon_type: CouponType,
    value: Decimal,
    min_subtotal: Decimal | None,
    max_discount: Decimal | None,
) -> None:
    """Business guardrails beyond schema bounds (shared with the Phase 7
    promo agent's guardrail — AI drafts obey the same physics)."""
    if coupon_type == CouponType.PCT:
        if value > MAX_PCT_VALUE:
            raise HTTPException(status_code=422, detail=f"PCT value capped at {MAX_PCT_VALUE}%")
        if max_discount is None:
            raise HTTPException(status_code=422, detail="PCT coupons need max_discount")
    else:  # FLAT
        if value > MAX_FLAT_VALUE:
            raise HTTPException(status_code=422, detail=f"FLAT value capped at ₹{MAX_FLAT_VALUE}")
        if min_subtotal is None or min_subtotal < value * 2:
            raise HTTPException(
                status_code=422,
                detail="FLAT coupons need min_subtotal ≥ 2× value",
            )


async def _times_used(session: AsyncSession, coupon_id: int) -> int:
    return (
        await session.scalar(select(func.count()).where(CouponRedemption.coupon_id == coupon_id))
    ) or 0


async def _out(session: AsyncSession, coupon: Coupon) -> CouponOut:
    out = CouponOut.model_validate(coupon)
    out.times_used = await _times_used(session, coupon.id)
    return out


async def _get(session: AsyncSession, coupon_id: int) -> Coupon:
    coupon = await session.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    return coupon


@router.get("", response_model=list[CouponOut])
async def list_coupons(
    session: SessionDep,
    admin: User = AdminUser,
    active: Annotated[bool | None, Query()] = None,
) -> list[CouponOut]:
    stmt = select(Coupon).order_by(Coupon.id.desc())
    if active is not None:
        stmt = stmt.where(Coupon.is_active == active)
    return [await _out(session, c) for c in (await session.scalars(stmt)).all()]


@router.post("", response_model=CouponOut, status_code=201)
async def create_coupon(
    body: CouponCreateIn, session: SessionDep, admin: User = AdminUser
) -> CouponOut:
    validate_coupon_values(body.type, body.value, body.min_subtotal, body.max_discount)
    exists = await session.scalar(select(Coupon).where(Coupon.code == body.code))
    if exists is not None:
        raise HTTPException(status_code=409, detail=f"Coupon {body.code} already exists")
    coupon = Coupon(**body.model_dump(), source="MANUAL")
    session.add(coupon)
    audit.record(
        session, actor=admin, action="coupon.create", entity="coupon", detail={"code": body.code}
    )
    await session.commit()
    return await _out(session, coupon)


@router.patch("/{coupon_id}", response_model=CouponOut)
async def update_coupon(
    coupon_id: int, body: CouponUpdateIn, session: SessionDep, admin: User = AdminUser
) -> CouponOut:
    coupon = await _get(session, coupon_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")
    for field, value in changes.items():
        setattr(coupon, field, value)
    validate_coupon_values(coupon.type, coupon.value, coupon.min_subtotal, coupon.max_discount)
    audit.record(
        session,
        actor=admin,
        action="coupon.update",
        entity="coupon",
        detail={"code": coupon.code, "fields": sorted(changes)},
    )
    await session.commit()
    return await _out(session, coupon)


@router.delete("/{coupon_id}", status_code=204)
async def delete_coupon(coupon_id: int, session: SessionDep, admin: User = AdminUser) -> None:
    coupon = await _get(session, coupon_id)
    if await _times_used(session, coupon.id):
        # Redeemed coupons are history (order rows reference them) — deactivate instead.
        raise HTTPException(status_code=409, detail="Coupon has redemptions — deactivate it")
    audit.record(
        session, actor=admin, action="coupon.delete", entity="coupon", detail={"code": coupon.code}
    )
    await session.delete(coupon)
    await session.commit()

"""Admin promo suggestions (Phase 7): AI drafts → the approval flows.

POST /api/v1/admin/promos/suggest runs the promo agent, then persists what
survives SERVER-SIDE re-validation as:
- combos: source=AI_SUGGESTED, status=DRAFT   (existing combo approval flow)
- coupons: source=AI_SUGGESTED, is_active=False (coupon activation flow)

The agent can propose; only an owner/admin can make anything customer-
visible. The api re-checks everything it persists (item ids exist, combo
price ≤ parts, coupon value guardrails) — the ai guardrail is trusted the
way any client is: not at all.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import Combo, Coupon, MenuItem, User
from dosadash_api.db.session import get_session
from dosadash_api.routers.admin_coupons import validate_coupon_values
from dosadash_api.services import audit
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_shared import ComboOut, CouponOut, PromoStats, Role

router = APIRouter(prefix="/api/v1/admin/promos", tags=["admin:promos"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)


class PromoSuggestOut(BaseModel):
    combos: list[ComboOut]
    coupons: list[CouponOut]
    skipped: list[str]  # human-readable reasons for anything not persisted
    stats: PromoStats | None
    model: str | None
    fallback: bool


@router.post("/suggest", response_model=PromoSuggestOut)
async def suggest(
    session: SessionDep,
    ai: Annotated[AIClient, Depends(get_ai_client)],
    admin: User = AdminUser,
) -> PromoSuggestOut:
    try:
        result = await ai.suggest_promos(admin_user_id=admin.id)
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail="Promo agent unavailable") from exc

    skipped: list[str] = []
    created_combos: list[Combo] = []
    created_coupons: list[Coupon] = []

    existing_sets = {
        tuple(sorted(row.item_ids)) for row in (await session.execute(select(Combo.item_ids))).all()
    }
    for combo in result.combos:
        key = tuple(sorted(combo.item_ids))
        if key in existing_sets:
            skipped.append(f"combo {combo.name}: pair already has a combo")
            continue
        rows = (
            await session.scalars(select(MenuItem).where(MenuItem.id.in_(set(combo.item_ids))))
        ).all()
        if len(rows) != len(set(combo.item_ids)):
            skipped.append(f"combo {combo.name}: unknown item ids")
            continue
        parts_total = sum((m.price for m in rows), Decimal("0"))
        if combo.price > parts_total:
            skipped.append(f"combo {combo.name}: price exceeds sum of parts")
            continue
        row = Combo(
            name=combo.name,
            item_ids=list(combo.item_ids),
            price=combo.price,
            source="AI_SUGGESTED",
            status="DRAFT",
        )
        session.add(row)
        existing_sets.add(key)
        created_combos.append(row)

    for coupon in result.coupons:
        exists = await session.scalar(select(Coupon).where(Coupon.code == coupon.code))
        if exists is not None:
            skipped.append(f"coupon {coupon.code}: code already exists")
            continue
        try:
            validate_coupon_values(
                coupon.type, coupon.value, coupon.min_subtotal, coupon.max_discount
            )
        except HTTPException as exc:
            skipped.append(f"coupon {coupon.code}: {exc.detail}")
            continue
        row = Coupon(
            code=coupon.code,
            type=coupon.type,
            value=coupon.value,
            description=coupon.description,
            min_subtotal=coupon.min_subtotal,
            max_discount=coupon.max_discount,
            is_active=False,
            source="AI_SUGGESTED",
        )
        session.add(row)
        created_coupons.append(row)

    audit.record(
        session,
        actor=admin,
        action="promo.suggest",
        entity="promo",
        detail={
            "combos": [c.name for c in created_combos],
            "coupons": [c.code for c in created_coupons],
            "skipped": len(skipped),
            "model": result.model,
            "fallback": result.fallback,
        },
    )
    await session.commit()
    coupon_out = []
    for c in created_coupons:
        out = CouponOut.model_validate(c)
        out.times_used = 0
        coupon_out.append(out)
    return PromoSuggestOut(
        combos=[ComboOut.model_validate(c) for c in created_combos],
        coupons=coupon_out,
        skipped=skipped,
        stats=result.stats,
        model=result.model,
        fallback=result.fallback,
    )

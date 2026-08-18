"""Wastage log (Phase 6): stock write-offs with an immutable audit trail.

POST decrements `ingredients.stock_qty` atomically with the log row (same
transaction). Stock is clamped at 0 — kitchens routinely discover wastage
that was never counted in, so refusing the entry would just push staff to
falsify quantities. The clamp is recorded in the audit detail.

kitchen_staff can log wastage (they're the ones binning it); admin/owner too.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dosadash_api import events
from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import Ingredient, User, WastageEntry
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_shared import Role, WastageIn, WastageOut

router = APIRouter(prefix="/api/v1/admin/wastage", tags=["admin:wastage"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
StaffUser = require_role(Role.KITCHEN_STAFF, Role.ADMIN, Role.OWNER)


def _to_out(entry: WastageEntry) -> WastageOut:
    return WastageOut(
        id=entry.id,
        ingredient_id=entry.ingredient_id,
        ingredient_name=entry.ingredient.name,
        unit=entry.ingredient.unit,
        qty=entry.qty,
        reason=entry.reason,
        note=entry.note,
        recorded_by=entry.recorded_by,
        stock_after=entry.stock_after,
        at=entry.at,
    )


@router.get("", response_model=list[WastageOut])
async def list_wastage(
    session: SessionDep,
    staff: User = StaffUser,
    ingredient_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[WastageOut]:
    query = (
        select(WastageEntry)
        .options(selectinload(WastageEntry.ingredient))
        .order_by(WastageEntry.at.desc(), WastageEntry.id.desc())
        .limit(limit)
    )
    if ingredient_id is not None:
        query = query.where(WastageEntry.ingredient_id == ingredient_id)
    rows = (await session.scalars(query)).all()
    return [_to_out(e) for e in rows]


@router.post("", response_model=WastageOut, status_code=201)
async def log_wastage(body: WastageIn, session: SessionDep, staff: User = StaffUser) -> WastageOut:
    ingredient = await session.get(Ingredient, body.ingredient_id, with_for_update=True)
    if ingredient is None:
        raise HTTPException(status_code=404, detail="Ingredient not found")

    stock_before = ingredient.stock_qty
    stock_after = max(stock_before - body.qty, Decimal("0"))
    clamped = stock_before - body.qty < 0
    ingredient.stock_qty = stock_after

    entry = WastageEntry(
        ingredient_id=ingredient.id,
        qty=body.qty,
        reason=body.reason,
        note=body.note,
        recorded_by=staff.id,
        stock_after=stock_after,
    )
    session.add(entry)
    audit.record(
        session,
        actor=staff,
        action="wastage.log",
        entity=f"ingredient:{ingredient.id}",
        detail={
            "qty": str(body.qty),
            "reason": body.reason,
            "stock_before": str(stock_before),
            "stock_after": str(stock_after),
            "clamped": clamped,
        },
    )
    await session.commit()
    await session.refresh(entry, ["at"])
    entry.ingredient = ingredient
    await events.publish_inventory_event(
        "inventory.wastage",
        detail={
            "ingredient_id": ingredient.id,
            "qty": str(body.qty),
            "stock_after": str(stock_after),
        },
    )
    return _to_out(entry)

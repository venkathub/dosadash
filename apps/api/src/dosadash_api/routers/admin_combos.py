"""Admin combo builder (Phase 2): CRUD + approval flow.

Combos are born DRAFT and only surface publicly once APPROVED. Phase 7 AI
combo suggestions land as source=AI_SUGGESTED drafts in this same flow —
the owner-approval story is built now, the AI feeds it later.

Every item_id is DB-validated (Hard Rule 2 spirit) and the combo price must
not exceed the sum of its parts. Mutations audit + publish combo.* events
on the menu channel (Hard Rule 4).
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import events
from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import Combo, MenuItem, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_shared import ComboCreateIn, ComboOut, ComboStatusIn, ComboUpdateIn, Role

router = APIRouter(prefix="/api/v1/admin/combos", tags=["admin:combos"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)


async def _validate_combo(session: AsyncSession, item_ids: list[int], price: Decimal) -> None:
    """All item_ids must exist and the combo must actually be a deal."""
    rows = (await session.scalars(select(MenuItem).where(MenuItem.id.in_(set(item_ids))))).all()
    found = {m.id: m for m in rows}
    missing = set(item_ids) - set(found)
    if missing:
        raise HTTPException(status_code=404, detail=f"unknown item ids: {sorted(missing)}")
    parts_total = sum((found[i].price for i in item_ids), Decimal("0"))
    if price > parts_total:
        raise HTTPException(
            status_code=422,
            detail=f"combo price {price} exceeds sum of items {parts_total}",
        )


async def _get_combo(session: AsyncSession, combo_id: int) -> Combo:
    combo = await session.get(Combo, combo_id)
    if combo is None:
        raise HTTPException(status_code=404, detail="Combo not found")
    return combo


@router.get("", response_model=list[ComboOut])
async def list_combos(
    session: SessionDep,
    admin: User = AdminUser,
    status: Annotated[str | None, Query(pattern="^(DRAFT|APPROVED|REJECTED)$")] = None,
) -> list[ComboOut]:
    stmt = select(Combo).order_by(Combo.id.desc())
    if status:
        stmt = stmt.where(Combo.status == status)
    return [ComboOut.model_validate(c) for c in (await session.scalars(stmt)).all()]


@router.post("", response_model=ComboOut, status_code=201)
async def create_combo(
    body: ComboCreateIn, session: SessionDep, admin: User = AdminUser
) -> ComboOut:
    await _validate_combo(session, body.item_ids, body.price)
    combo = Combo(name=body.name, item_ids=body.item_ids, price=body.price, source="MANUAL")
    session.add(combo)
    audit.record(
        session, actor=admin, action="combo.create", entity="combo", detail={"name": body.name}
    )
    await session.commit()
    await events.publish_catalog_event(
        "combo.created", detail={"combo_id": combo.id, "name": combo.name}
    )
    return ComboOut.model_validate(combo)


@router.patch("/{combo_id}", response_model=ComboOut)
async def update_combo(
    combo_id: int, body: ComboUpdateIn, session: SessionDep, admin: User = AdminUser
) -> ComboOut:
    combo = await _get_combo(session, combo_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")
    new_item_ids = changes.get("item_ids", combo.item_ids)
    new_price = changes.get("price", combo.price)
    await _validate_combo(session, new_item_ids, new_price)
    for field, value in changes.items():
        setattr(combo, field, value)
    audit.record(
        session,
        actor=admin,
        action="combo.update",
        entity=f"combo:{combo.id}",
        detail={"fields": sorted(changes)},
    )
    await session.commit()
    await events.publish_catalog_event(
        "combo.updated", detail={"combo_id": combo.id, "fields": sorted(changes)}
    )
    return ComboOut.model_validate(combo)


@router.post("/{combo_id}/status", response_model=ComboOut)
async def set_combo_status(
    combo_id: int, body: ComboStatusIn, session: SessionDep, admin: User = AdminUser
) -> ComboOut:
    """Approve or reject (either direction — a pulled combo can be re-approved)."""
    combo = await _get_combo(session, combo_id)
    if combo.status == body.status:
        raise HTTPException(status_code=409, detail=f"Combo already {body.status}")
    if body.status == "APPROVED":
        # items may have changed since drafting — re-validate before it goes live
        await _validate_combo(session, combo.item_ids, combo.price)
    previous = combo.status
    combo.status = body.status
    audit.record(
        session,
        actor=admin,
        action="combo.status",
        entity=f"combo:{combo.id}",
        detail={"from": previous, "to": body.status},
    )
    await session.commit()
    await events.publish_catalog_event(
        "combo.status", detail={"combo_id": combo.id, "status": body.status}
    )
    return ComboOut.model_validate(combo)


@router.delete("/{combo_id}", status_code=204)
async def delete_combo(combo_id: int, session: SessionDep, admin: User = AdminUser) -> None:
    combo = await _get_combo(session, combo_id)
    name = combo.name
    audit.record(
        session,
        actor=admin,
        action="combo.delete",
        entity=f"combo:{combo_id}",
        detail={"name": name},
    )
    await session.delete(combo)
    await session.commit()
    await events.publish_catalog_event("combo.deleted", detail={"combo_id": combo_id, "name": name})

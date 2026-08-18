"""Admin supplier master (Phase 6): CRUD backing purchase orders.

Suppliers were promoted from the free-text `ingredients.supplier` column
(migration c9d4e82f7a13). Deleting a supplier referenced by ingredients or
purchase orders is refused (409) — POs are financial records; FK SET NULL
would silently orphan them.

Mutations publish supplier.* on the inventory channel (Hard Rule 4) so the
Phase 6 inventory agent never drafts against a stale supplier list.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api import events
from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import Ingredient, PurchaseOrder, Supplier, User
from dosadash_api.db.session import get_session
from dosadash_api.services import audit
from dosadash_shared import Role, SupplierIn, SupplierOut, SupplierUpdateIn

router = APIRouter(prefix="/api/v1/admin/suppliers", tags=["admin:suppliers"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)


async def _get_supplier(session: AsyncSession, supplier_id: int) -> Supplier:
    supplier = await session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


@router.get("", response_model=list[SupplierOut])
async def list_suppliers(session: SessionDep, admin: User = AdminUser) -> list[SupplierOut]:
    rows = (await session.scalars(select(Supplier).order_by(Supplier.name))).all()
    return [SupplierOut.model_validate(s) for s in rows]


@router.post("", response_model=SupplierOut, status_code=201)
async def create_supplier(
    body: SupplierIn, session: SessionDep, admin: User = AdminUser
) -> SupplierOut:
    supplier = Supplier(**body.model_dump())
    session.add(supplier)
    audit.record(
        session,
        actor=admin,
        action="supplier.create",
        entity="supplier",
        detail={"name": body.name},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Supplier name already exists") from exc
    await events.publish_inventory_event(
        "supplier.created", detail={"supplier_id": supplier.id, "name": supplier.name}
    )
    return SupplierOut.model_validate(supplier)


@router.patch("/{supplier_id}", response_model=SupplierOut)
async def update_supplier(
    supplier_id: int, body: SupplierUpdateIn, session: SessionDep, admin: User = AdminUser
) -> SupplierOut:
    supplier = await _get_supplier(session, supplier_id)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")
    for field, value in changes.items():
        setattr(supplier, field, value)
    audit.record(
        session,
        actor=admin,
        action="supplier.update",
        entity=f"supplier:{supplier.id}",
        detail={"fields": sorted(changes)},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Supplier name already exists") from exc
    await events.publish_inventory_event(
        "supplier.updated", detail={"supplier_id": supplier.id, "fields": sorted(changes)}
    )
    return SupplierOut.model_validate(supplier)


@router.delete("/{supplier_id}", status_code=204)
async def delete_supplier(supplier_id: int, session: SessionDep, admin: User = AdminUser) -> None:
    supplier = await _get_supplier(session, supplier_id)
    used_by_ingredient = await session.scalar(
        select(Ingredient.id).where(Ingredient.supplier_id == supplier_id).limit(1)
    )
    used_by_po = await session.scalar(
        select(PurchaseOrder.id).where(PurchaseOrder.supplier_id == supplier_id).limit(1)
    )
    if used_by_ingredient is not None or used_by_po is not None:
        raise HTTPException(
            status_code=409,
            detail="Supplier is referenced by ingredients or purchase orders — "
            "deactivate it instead (is_active=false)",
        )
    name = supplier.name
    audit.record(
        session,
        actor=admin,
        action="supplier.delete",
        entity=f"supplier:{supplier_id}",
        detail={"name": name},
    )
    await session.delete(supplier)
    await session.commit()
    await events.publish_inventory_event(
        "supplier.deleted", detail={"supplier_id": supplier_id, "name": name}
    )

"""Purchase-order state machine (Phase 6) — ALL PO status changes go through
here (same convention as order_service for orders):

    DRAFT → PENDING_APPROVAL → APPROVED → RECEIVED
                    ↓              ↓
                REJECTED       CANCELLED

Pure DB layer: callers own commit and event publishing. RECEIVED is the only
transition that touches stock — it increments `ingredients.stock_qty` line
by line, closing the loop the wastage log opens.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from dosadash_api.db.models import Ingredient, PurchaseOrder, PurchaseOrderItem
from dosadash_shared import InventoryDraftResult, POSource, POState

_ALLOWED: dict[POState, set[POState]] = {
    POState.DRAFT: {POState.PENDING_APPROVAL, POState.CANCELLED},
    POState.PENDING_APPROVAL: {POState.APPROVED, POState.REJECTED, POState.CANCELLED},
    POState.APPROVED: {POState.RECEIVED, POState.CANCELLED},
    POState.REJECTED: set(),
    POState.RECEIVED: set(),
    POState.CANCELLED: set(),
}

OPEN_STATES = (POState.DRAFT, POState.PENDING_APPROVAL, POState.APPROVED)


class InvalidPOTransition(Exception):
    def __init__(self, current: POState, target: POState) -> None:
        super().__init__(f"cannot move purchase order from {current} to {target}")
        self.current = current
        self.target = target


def _transition(po: PurchaseOrder, target: POState) -> None:
    if target not in _ALLOWED[po.status]:
        raise InvalidPOTransition(po.status, target)
    po.status = target


async def persist_agent_drafts(
    session: AsyncSession, result: InventoryDraftResult
) -> tuple[list[PurchaseOrder], list[int]]:
    """Store validated agent drafts as PENDING_APPROVAL POs (idempotent-ish:
    suppliers that already have an open AGENT PO are skipped, so a nightly
    re-run never stacks duplicate drafts). Returns (created, skipped_ids
    where id is the supplier_id or 0 for unassigned).

    Caller commits and publishes inventory.po_drafted events.
    """
    open_supplier_ids = set(
        (
            await session.scalars(
                select(PurchaseOrder.supplier_id).where(
                    PurchaseOrder.source == POSource.AGENT,
                    PurchaseOrder.status.in_(OPEN_STATES),
                )
            )
        ).all()
    )

    ingredient_ids = {line.ingredient_id for draft in result.drafts for line in draft.lines}
    ingredients = {
        i.id: i
        for i in (
            await session.scalars(select(Ingredient).where(Ingredient.id.in_(ingredient_ids)))
        ).all()
    }

    created: list[PurchaseOrder] = []
    skipped: list[int] = []
    for draft in result.drafts:
        if draft.supplier_id in open_supplier_ids:
            skipped.append(draft.supplier_id or 0)
            continue
        expected = Decimal("0")
        items: list[PurchaseOrderItem] = []
        for line in draft.lines:
            ingredient = ingredients.get(line.ingredient_id)
            if ingredient is None:  # belt & braces — ai already validated
                continue
            if ingredient.cost is not None:
                expected += line.qty * ingredient.cost
            items.append(
                PurchaseOrderItem(
                    ingredient_id=ingredient.id,
                    qty=line.qty,
                    unit=ingredient.unit,
                    unit_cost=ingredient.cost,
                    reason=line.reason,
                )
            )
        if not items:
            continue
        po = PurchaseOrder(
            supplier_id=draft.supplier_id,
            status=POState.PENDING_APPROVAL,
            source=POSource.AGENT,
            rationale=draft.rationale,
            coverage_days=result.coverage_days,
            expected_cost=expected if expected else None,
            model=result.model,
            prompt_version=result.prompt_version,
            items=items,
        )
        session.add(po)
        created.append(po)
    await session.flush()
    return created, skipped


async def get_po(session: AsyncSession, po_id: int) -> PurchaseOrder | None:
    return await session.scalar(
        select(PurchaseOrder)
        .options(
            selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.ingredient),
            selectinload(PurchaseOrder.supplier),
        )
        .where(PurchaseOrder.id == po_id)
    )


def submit(po: PurchaseOrder) -> None:
    _transition(po, POState.PENDING_APPROVAL)


def approve(po: PurchaseOrder, *, actor_id: int) -> None:
    _transition(po, POState.APPROVED)
    po.approved_by = actor_id
    po.approved_at = datetime.now(UTC)


def reject(po: PurchaseOrder, *, actor_id: int) -> None:
    _transition(po, POState.REJECTED)
    po.approved_by = actor_id
    po.approved_at = datetime.now(UTC)


def cancel(po: PurchaseOrder) -> None:
    _transition(po, POState.CANCELLED)


async def receive(session: AsyncSession, po: PurchaseOrder) -> None:
    """Goods in: APPROVED → RECEIVED, stock incremented per line."""
    _transition(po, POState.RECEIVED)
    po.received_at = datetime.now(UTC)
    for item in po.items:
        ingredient = await session.get(Ingredient, item.ingredient_id, with_for_update=True)
        if ingredient is not None:
            ingredient.stock_qty = ingredient.stock_qty + item.qty

"""Phase 6: deterministic needs math (real DB) + PO state machine tests."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from dosadash_ai.inventory.needs import compute_needs
from dosadash_api.db.models import Forecast, Ingredient, MenuItem, PurchaseOrder, Supplier, User
from dosadash_api.services import po_service
from dosadash_shared import (
    IngredientNeed,
    InventoryDraftResult,
    PODraft,
    PODraftLine,
    POSource,
    POState,
    Role,
)

TODAY = date(2026, 8, 18)


async def _seed_forecasts(db_session, item_name: str, qty_per_day: float, days: int = 7) -> None:
    item_id = await db_session.scalar(select(MenuItem.id).where(MenuItem.name == item_name))
    for offset in range(days):
        db_session.add(
            Forecast(
                item_id=item_id,
                date=TODAY + timedelta(days=offset),
                predicted_qty=qty_per_day,
                model_version="test/v1",
            )
        )
    await db_session.commit()


async def _set_ingredient(db_session, name: str, *, stock: str, reorder: str = "0") -> Ingredient:
    ingredient = await db_session.scalar(select(Ingredient).where(Ingredient.name == name))
    ingredient.stock_qty = Decimal(stock)
    ingredient.reorder_point = Decimal(reorder)
    await db_session.commit()
    return ingredient


# ------------------------------------------------------------------ needs math


async def test_compute_needs_stock_vs_forecast(db_session):
    # Masala Dosa and Lemon Rice both use 1 kg idli rice per unit (conftest seed)
    await _seed_forecasts(db_session, "Masala Dosa", 10.0)  # 70 over 7 days
    await _seed_forecasts(db_session, "Lemon Rice", 5.0)  # 35 over 7 days
    await _set_ingredient(db_session, "idli rice", stock="30", reorder="5")
    # milk: coffee forecast small, stock plenty → no deficit
    await _seed_forecasts(db_session, "Filter Coffee", 2.0)
    await _set_ingredient(db_session, "milk", stock="500")

    needs = await compute_needs(db_session, coverage_days=7, today=TODAY)
    by_name = {n.name: n for n in needs}

    rice = by_name["idli rice"]
    assert rice.need_qty == Decimal("105.000")  # 70 + 35
    assert rice.deficit_qty == Decimal("80.000")  # 105 + 5 − 30
    assert "milk" not in by_name  # covered
    # peanut used by lemon rice → 35 need vs 0 stock
    assert by_name["peanut"].deficit_qty == Decimal("35.000")


async def test_compute_needs_respects_coverage_window(db_session):
    await _seed_forecasts(db_session, "Masala Dosa", 10.0, days=14)
    await _set_ingredient(db_session, "idli rice", stock="0")
    three = await compute_needs(db_session, coverage_days=3, today=TODAY)
    assert next(n for n in three if n.name == "idli rice").need_qty == Decimal("30.000")


# ------------------------------------------------------------ persist + machine


def _result(*drafts: PODraft) -> InventoryDraftResult:
    return InventoryDraftResult(
        coverage_days=7, drafts=list(drafts), model="gpt-4o-mini", fallback=False
    )


async def _rice_and_supplier(db_session) -> tuple[Ingredient, Supplier]:
    supplier = Supplier(name="Madurai Traders")
    db_session.add(supplier)
    await db_session.flush()
    rice = await db_session.scalar(select(Ingredient).where(Ingredient.name == "idli rice"))
    rice.supplier_id = supplier.id
    rice.cost = Decimal("60")
    await db_session.commit()
    return rice, supplier


async def test_persist_agent_drafts_and_skip_open_duplicates(db_session):
    rice, supplier = await _rice_and_supplier(db_session)
    draft = PODraft(
        supplier_id=supplier.id,
        lines=[PODraftLine(ingredient_id=rice.id, qty=Decimal("25"), reason="deficit 20 kg")],
        rationale="Weekend dosa demand.",
    )

    created, skipped = await po_service.persist_agent_drafts(db_session, _result(draft))
    await db_session.commit()
    assert len(created) == 1 and skipped == []
    po = await po_service.get_po(db_session, created[0].id)
    assert po.status == POState.PENDING_APPROVAL
    assert po.source == POSource.AGENT
    assert po.expected_cost == Decimal("1500")  # 25 × 60
    assert po.items[0].unit == "kg"
    assert po.items[0].unit_cost == Decimal("60")

    # nightly re-run: same supplier still open → skipped, nothing stacked
    created2, skipped2 = await po_service.persist_agent_drafts(db_session, _result(draft))
    await db_session.commit()
    assert created2 == [] and skipped2 == [supplier.id]
    count = len((await db_session.scalars(select(PurchaseOrder))).all())
    assert count == 1


async def test_po_state_machine_receive_increments_stock(db_session):
    rice, supplier = await _rice_and_supplier(db_session)
    rice_stock_before = rice.stock_qty
    draft = PODraft(
        supplier_id=supplier.id,
        lines=[PODraftLine(ingredient_id=rice.id, qty=Decimal("25"), reason="deficit")],
        rationale="Restock.",
    )
    created, _ = await po_service.persist_agent_drafts(db_session, _result(draft))
    await db_session.commit()
    po = await po_service.get_po(db_session, created[0].id)

    owner = User(phone="+919555559001", name="Owner", role=Role.OWNER)
    db_session.add(owner)
    await db_session.commit()

    # PENDING_APPROVAL → RECEIVED is illegal (must approve first)
    with pytest.raises(po_service.InvalidPOTransition):
        await po_service.receive(db_session, po)

    po_service.approve(po, actor_id=owner.id)
    assert po.status == POState.APPROVED
    await po_service.receive(db_session, po)
    await db_session.commit()

    assert po.status == POState.RECEIVED
    assert po.received_at is not None
    await db_session.refresh(rice)
    assert rice.stock_qty == rice_stock_before + Decimal("25")

    # terminal: no further transitions
    with pytest.raises(po_service.InvalidPOTransition):
        po_service.cancel(po)


async def test_po_reject_is_terminal(db_session):
    rice, supplier = await _rice_and_supplier(db_session)
    draft = PODraft(
        supplier_id=supplier.id,
        lines=[PODraftLine(ingredient_id=rice.id, qty=Decimal("5"), reason="deficit")],
        rationale="Restock.",
    )
    owner = User(phone="+919555559002", name="Owner", role=Role.OWNER)
    db_session.add(owner)
    await db_session.flush()
    created, _ = await po_service.persist_agent_drafts(db_session, _result(draft))
    po = created[0]
    po_service.reject(po, actor_id=owner.id)
    await db_session.commit()
    assert po.status == POState.REJECTED
    assert po.approved_by == owner.id
    with pytest.raises(po_service.InvalidPOTransition):
        po_service.approve(po, actor_id=owner.id)


async def test_needs_line_unused_ingredient_never_appears(db_session):
    """An IngredientNeed can only exist for recipe-mapped ingredients —
    chicken has no forecast here, so no needs row at all."""
    needs = await compute_needs(db_session, coverage_days=7, today=TODAY)
    assert needs == []


def test_need_schema_rejects_bad_rows():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IngredientNeed(ingredient_id=1, name="x")  # missing quantities

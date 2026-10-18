"""Crash/replay, concurrent delivery, approval and recovery integration checks."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from dosadash_api.db.models import Ingredient, InventoryWorkflow, PurchaseOrder
from dosadash_api.services import inventory_workflow as wf
from dosadash_api.services import po_service
from dosadash_shared import InventoryDraftResult, PODraft, PODraftLine, POState


async def planner_for(session):
    rice = await session.scalar(select(Ingredient).where(Ingredient.name == "idli rice"))
    return AsyncMock(
        return_value=InventoryDraftResult(
            coverage_days=7,
            drafts=[
                PODraft(
                    lines=[PODraftLine(ingredient_id=rice.id, qty=Decimal("5"), reason="deficit")],
                    rationale="Synthetic recovery test",
                )
            ],
        )
    )


async def test_replay_after_restart_never_duplicates_or_regenerates(db_session):
    planner = await planner_for(db_session)
    run, fresh = await wf.advance(db_session, key="restart", coverage_days=7, planner=planner)
    assert fresh and run.stage == "AWAITING_APPROVAL"
    original = list(run.po_ids)
    # A different session represents process restart; replay uses persisted output.
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async with maker() as other:
        run, fresh = await wf.advance(other, key="restart", coverage_days=7, planner=planner)
        assert not fresh and run.po_ids == original
    assert planner.await_count == 1
    assert await db_session.scalar(select(func.count()).select_from(PurchaseOrder)) == 1
    with pytest.raises(ValueError, match="different coverage"):
        await wf.advance(db_session, key="restart", coverage_days=3, planner=planner)


async def test_crash_after_model_checkpoint_resumes_without_model(db_session, monkeypatch):
    planner = await planner_for(db_session)
    original = po_service.persist_agent_drafts
    monkeypatch.setattr(
        po_service, "persist_agent_drafts", AsyncMock(side_effect=RuntimeError("crash"))
    )
    with pytest.raises(RuntimeError):
        await wf.advance(db_session, key="crash", coverage_days=7, planner=planner)
    await db_session.rollback()
    assert (await db_session.get(InventoryWorkflow, "crash")).stage == "READY"
    monkeypatch.setattr(po_service, "persist_agent_drafts", original)
    run, fresh = await wf.advance(db_session, key="crash", coverage_days=7, planner=planner)
    assert fresh and len(run.po_ids) == 1 and planner.await_count == 1


async def test_live_lease_blocks_and_expired_lease_recovers(db_session):
    planner = await planner_for(db_session)
    run = InventoryWorkflow(
        key="lease",
        coverage_days=7,
        stage="PLANNING",
        attempts=1,
        lease_token="old",
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(run)
    await db_session.commit()
    with pytest.raises(wf.WorkflowBusy):
        await wf.advance(db_session, key="lease", coverage_days=7, planner=planner)
    run.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    run, _ = await wf.advance(db_session, key="lease", coverage_days=7, planner=planner)
    assert run.attempts == 2 and planner.await_count == 1


async def test_repeated_failure_stops_recovery(db_session):
    planner = AsyncMock(side_effect=RuntimeError("provider private payload"))
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await wf.advance(db_session, key="failed", coverage_days=7, planner=planner)
    run = await db_session.get(InventoryWorkflow, "failed")
    assert run.stage == "FAILED" and run.error == "RuntimeError"
    with pytest.raises(ValueError, match="exhausted"):
        await wf.advance(db_session, key="failed", coverage_days=7, planner=planner)
    assert planner.await_count == 3


async def test_waits_for_human_then_receives_only_once(db_session):
    planner = await planner_for(db_session)
    run, _ = await wf.advance(db_session, key="human", coverage_days=7, planner=planner)
    po = await po_service.get_po(db_session, run.po_ids[0])
    with pytest.raises(po_service.InvalidPOTransition):
        await po_service.receive(db_session, po)
    # Simulate an already authorized approval; API RBAC is covered separately.
    po.status = POState.APPROVED
    await db_session.commit()
    run, _ = await wf.advance(db_session, key="human", coverage_days=7, planner=planner)
    assert run.stage == "AWAITING_RECEIPT"
    po = await po_service.get_po(db_session, po.id)
    rice = await db_session.get(Ingredient, po.items[0].ingredient_id)
    before = rice.stock_qty
    await po_service.receive(db_session, po)
    await db_session.commit()
    with pytest.raises(po_service.InvalidPOTransition):
        await po_service.receive(db_session, po)
    assert rice.stock_qty == before + Decimal("5")
    run, _ = await wf.advance(db_session, key="human", coverage_days=7, planner=planner)
    assert run.stage == "COMPLETED"


async def test_concurrent_distinct_runs_do_not_duplicate_supplier(db_session):
    import asyncio

    planner = await planner_for(db_session)
    await db_session.commit()
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def execute(key):
        async with maker() as session:
            run, _ = await wf.advance(session, key=key, coverage_days=7, planner=planner)
            return run.po_ids

    ids = await asyncio.gather(execute("one"), execute("two"))
    assert sum(map(len, ids)) == 1


async def test_concurrent_receipts_increment_stock_once(db_session):
    import asyncio

    planner = await planner_for(db_session)
    run, _ = await wf.advance(db_session, key="receive-race", coverage_days=7, planner=planner)
    po = await po_service.get_po(db_session, run.po_ids[0])
    po.status = POState.APPROVED
    rice_id = po.items[0].ingredient_id
    rice = await db_session.get(Ingredient, rice_id)
    before = rice.stock_qty
    await db_session.commit()
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def receive():
        async with maker() as session:
            order = await po_service.get_po(session, po.id)
            try:
                await po_service.receive(session, order)
                await session.commit()
                return "received"
            except po_service.InvalidPOTransition:
                return "already-received"

    assert sorted(await asyncio.gather(receive(), receive())) == ["already-received", "received"]
    await db_session.refresh(rice)
    assert rice.stock_qty == before + Decimal("5")

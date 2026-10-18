"""Persist generation before side effects; resume without repeating completed steps.

A lease fences slow/crashed model calls. POs and the persistence checkpoint commit
atomically. Human decisions live in the existing PO state machine. Notification
is still best-effort; the admin queue is the durable approval surface.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import InventoryWorkflow, PurchaseOrder
from dosadash_api.services import po_service
from dosadash_shared import InventoryDraftRequest, InventoryDraftResult, POState

Planner = Callable[[InventoryDraftRequest], Awaitable[InventoryDraftResult]]
LEASE_SECONDS = 660  # beyond the worker hard timeout (600s)
MAX_ATTEMPTS = 3


class WorkflowBusy(ValueError):
    pass


async def _locked(session: AsyncSession, key: str) -> InventoryWorkflow:
    row = await session.scalar(
        select(InventoryWorkflow)
        .where(InventoryWorkflow.key == key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ValueError("unknown inventory workflow")
    return row


async def refresh_stage(session: AsyncSession, run: InventoryWorkflow) -> None:
    if run.stage not in {"AWAITING_APPROVAL", "AWAITING_RECEIPT"}:
        return
    states = list(
        (
            await session.scalars(
                select(PurchaseOrder.status).where(PurchaseOrder.id.in_(run.po_ids))
            )
        ).all()
    )
    if len(states) != len(run.po_ids):
        raise ValueError("workflow purchase order missing")
    if any(s in {POState.PENDING_APPROVAL, POState.DRAFT} for s in states):
        run.stage = "AWAITING_APPROVAL"
    elif any(s == POState.APPROVED for s in states):
        run.stage = "AWAITING_RECEIPT"
    else:
        run.stage = "COMPLETED"


async def advance(
    session: AsyncSession,
    *,
    key: str,
    coverage_days: int,
    planner: Planner,
) -> tuple[InventoryWorkflow, bool]:
    """Owns checkpoint commits. Returns (run, newly_persisted).

    Reusing a key with different arguments fails. A crash before the READY
    checkpoint can repeat inference, but never duplicates a committed PO.
    """
    if not 1 <= coverage_days <= 14 or not 1 <= len(key) <= 160:
        raise ValueError("invalid inventory workflow request")
    await session.execute(
        insert(InventoryWorkflow)
        .values(
            key=key,
            coverage_days=coverage_days,
            stage="PLANNING",
            attempts=0,
            po_ids=[],
            skipped=[],
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )
    run = await _locked(session, key)
    if run.coverage_days != coverage_days:
        raise ValueError("idempotency key already used with a different coverage window")
    if run.stage == "FAILED":
        raise ValueError("workflow exhausted three attempts; inspect before creating a new run")
    if run.stage == "PLANNING":
        now = datetime.now(UTC)
        if run.lease_until and run.lease_until > now:
            raise WorkflowBusy("inventory workflow is already running")
        if run.attempts >= MAX_ATTEMPTS:
            run.stage = "FAILED"
            run.lease_token = None
            run.lease_until = None
            await session.commit()
            raise ValueError("workflow exhausted three attempts")
        token = str(uuid4())
        run.lease_token = token
        run.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        run.attempts += 1
        await session.commit()
        try:
            result = await planner(
                InventoryDraftRequest(
                    coverage_days=coverage_days,
                    session_id=f"inventory:{sha256(key.encode()).hexdigest()[:32]}",
                )
            )
        except Exception as exc:
            run = await _locked(session, key)
            if run.lease_token == token:
                run.error = type(exc).__name__  # no provider payloads / credentials
                run.lease_until = None
                run.lease_token = None
                if run.attempts >= MAX_ATTEMPTS:
                    run.stage = "FAILED"
                await session.commit()
            raise
        run = await _locked(session, key)
        if run.lease_token != token or run.stage != "PLANNING":
            raise WorkflowBusy("inventory workflow lease replaced; stale result discarded")
        run.checkpoint = result.model_dump(mode="json")
        run.stage = "READY"
        run.error = None
        run.lease_until = None
        run.lease_token = None
        await session.commit()
    run = await _locked(session, key)
    if run.stage == "READY":
        result = InventoryDraftResult.model_validate(run.checkpoint)
        created, skipped = await po_service.persist_agent_drafts(session, result)
        run.po_ids = [po.id for po in created]
        run.skipped = skipped
        run.stage = "AWAITING_APPROVAL" if created else "COMPLETED"
        await session.commit()  # side effects + checkpoint in ONE transaction
        return run, True
    await refresh_stage(session, run)
    await session.commit()
    return run, False

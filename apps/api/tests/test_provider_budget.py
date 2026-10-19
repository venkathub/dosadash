"""Real PostgreSQL concurrency, crash reservation and fail-closed admission checks."""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from dosadash_ai import config
from dosadash_ai.llm import budget
from dosadash_api.db.models import ProviderBudgetDay


@pytest.fixture
async def ledger(db_session, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_BUDGET_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER_DAILY_BUDGET_USD", "0.04")
    monkeypatch.setenv("AI_PROVIDER_DAILY_MAX_CALLS", "3")
    config.get_settings.cache_clear()
    monkeypatch.setattr(
        budget,
        "get_sessionmaker",
        lambda: async_sessionmaker(db_session.bind, expire_on_commit=False),
    )
    yield db_session
    config.get_settings.cache_clear()


async def test_concurrent_reservations_cannot_overspend_and_survive_new_sessions(ledger):
    results = await asyncio.gather(
        *(budget.reserve("chat") for _ in range(8)), return_exceptions=True
    )
    assert sum(isinstance(r, budget.Reservation) for r in results) == 2
    assert sum(isinstance(r, budget.ProviderBudgetExceeded) for r in results) == 6
    row = (await ledger.scalars(select(ProviderBudgetDay))).one()
    assert row.charged_usd == Decimal(".04") and row.calls == 2 and row.unknown_calls == 2
    with pytest.raises(budget.ProviderBudgetExceeded):
        await budget.reserve("chat")


async def test_settlement_exactly_once_unknown_cost_retained_and_call_cap(ledger, monkeypatch):
    first = await budget.reserve("chat")
    monkeypatch.setattr(budget.litellm, "completion_cost", lambda **kw: 0.001)
    await asyncio.gather(*(budget.settle(first, object(), model="test") for _ in range(3)))
    second = await budget.reserve("chat")
    await budget.settle(second)  # unavailable cost is not zero
    third = await budget.reserve("embedding")
    await budget.settle(third)
    row = (await ledger.scalars(select(ProviderBudgetDay))).one()
    assert row.charged_usd == Decimal(".026") and row.known_usd == Decimal(".001")
    assert row.unknown_calls == 2 and row.calls == 3
    with pytest.raises(budget.ProviderBudgetExceeded):
        await budget.reserve("embedding")


async def test_ledger_failure_and_unpriced_operation_fail_closed(ledger, monkeypatch):
    with pytest.raises(budget.ProviderBudgetExceeded):
        await budget.reserve("unpriced")

    def failed():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(budget, "get_sessionmaker", failed)
    with pytest.raises(budget.ProviderBudgetExceeded, match="unavailable"):
        await budget.reserve("chat")


async def test_real_cost_above_estimate_blocks_next_admission(ledger, monkeypatch):
    r = await budget.reserve("chat")
    monkeypatch.setattr(budget.litellm, "completion_cost", lambda **kw: 0.10)
    await budget.settle(r, object(), model="test")
    with pytest.raises(budget.ProviderBudgetExceeded):
        await budget.reserve("embedding")

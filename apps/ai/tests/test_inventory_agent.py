"""Inventory agent guardrail + flow tests (no provider keys needed)."""

from decimal import Decimal

import pytest

from dosadash_ai.inventory import agent as inventory_agent
from dosadash_ai.inventory.guardrail import (
    deterministic_drafts,
    group_by_supplier,
    sanitize_batch,
)
from dosadash_ai.llm.client import LLMError
from dosadash_shared import (
    IngredientNeed,
    PODraft,
    PODraftBatch,
    PODraftLine,
)


def need(
    ingredient_id: int,
    name: str,
    deficit: str,
    *,
    supplier_id: int | None = None,
    supplier_name: str | None = None,
    unit: str = "kg",
    cost: str | None = "80",
) -> IngredientNeed:
    return IngredientNeed(
        ingredient_id=ingredient_id,
        name=name,
        unit=unit,
        stock_qty=Decimal("1"),
        reorder_point=Decimal("0.5"),
        need_qty=Decimal(deficit) + Decimal("0.5"),
        deficit_qty=Decimal(deficit),
        unit_cost=Decimal(cost) if cost else None,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
    )


@pytest.fixture
def needs() -> dict[int, IngredientNeed]:
    return {
        1: need(1, "idli rice", "10", supplier_id=5, supplier_name="Madurai Traders"),
        2: need(2, "urad dal", "4", supplier_id=5, supplier_name="Madurai Traders"),
        3: need(3, "milk", "6", supplier_id=None, unit="l"),
    }


def batch(*drafts: PODraft) -> PODraftBatch:
    return PODraftBatch(drafts=list(drafts))


def line(ingredient_id: int, qty: str, reason: str = "restock") -> PODraftLine:
    return PODraftLine(ingredient_id=ingredient_id, qty=Decimal(qty), reason=reason)


# -------------------------------------------------------------------- guardrail


def test_hallucinated_ingredient_dropped(needs):
    lines, _, violations = sanitize_batch(
        batch(PODraft(lines=[line(999, "5"), line(1, "12")], rationale="restock")), needs
    )
    ids = {ln.ingredient_id for ln in lines}
    assert 999 not in ids
    assert any("unknown" in v for v in violations)
    # every real need still covered
    assert ids == {1, 2, 3}


def test_quantities_clamped_to_deficit_band(needs):
    lines, _, violations = sanitize_batch(
        batch(
            PODraft(
                lines=[line(1, "2"), line(2, "400"), line(3, "7")],  # low, hoard, fine
                rationale="restock",
            )
        ),
        needs,
    )
    by_id = {ln.ingredient_id: ln.qty for ln in lines}
    assert by_id[1] == Decimal("10")  # raised to deficit
    assert by_id[2] == Decimal("12")  # capped at 3× deficit
    assert by_id[3] == Decimal("7")  # within band, untouched
    assert sum("raised" in v for v in violations) == 1
    assert sum("capped" in v for v in violations) == 1


def test_within_draft_duplicates_rejected_by_schema():
    """Duplicates inside ONE draft never reach the guardrail: PODraft schema
    validation rejects them (structured_completion's repair loop handles it)."""
    with pytest.raises(ValueError, match="duplicate ingredient_id"):
        PODraft(lines=[line(1, "10"), line(1, "11")], rationale="dup rice")


def test_duplicate_lines_dropped_and_omissions_added(needs):
    lines, _, violations = sanitize_batch(
        batch(
            PODraft(lines=[line(1, "10")], rationale="rice"),
            PODraft(lines=[line(1, "11"), line(2, "5")], rationale="rice again + dal"),
        ),
        needs,
    )
    assert sorted(ln.ingredient_id for ln in lines) == [1, 2, 3]
    assert any("duplicate" in v for v in violations)
    assert any("omitted" in v for v in violations)  # milk added at deficit
    milk = next(ln for ln in lines if ln.ingredient_id == 3)
    assert milk.qty == Decimal("6")


def test_grouping_is_by_canonical_supplier_not_llm(needs):
    # LLM put everything under one draft with the wrong supplier id
    lines, rationale, _ = sanitize_batch(
        batch(
            PODraft(
                supplier_id=42,
                lines=[line(1, "10"), line(2, "4"), line(3, "6")],
                rationale="one big order",
            )
        ),
        needs,
    )
    drafts = group_by_supplier(lines, needs, rationale=rationale)
    assert {d.supplier_id for d in drafts} == {5, None}
    supplier_po = next(d for d in drafts if d.supplier_id == 5)
    assert sorted(ln.ingredient_id for ln in supplier_po.lines) == [1, 2]


def test_deterministic_fallback_orders_exact_deficits(needs):
    drafts = deterministic_drafts(needs)
    all_lines = {ln.ingredient_id: ln for d in drafts for ln in d.lines}
    assert set(all_lines) == {1, 2, 3}
    assert all_lines[1].qty == Decimal("10")
    assert "stock" in all_lines[1].reason


# ------------------------------------------------------------------- agent flow


async def test_agent_falls_back_when_llm_unavailable(monkeypatch, needs):
    async def no_needs_llm(**_):
        raise LLMError("all models failed")

    async def fake_needs(session, *, coverage_days, today=None):
        return list(needs.values())

    monkeypatch.setattr(inventory_agent, "compute_needs", fake_needs)
    monkeypatch.setattr(inventory_agent, "structured_completion", no_needs_llm)

    result = await inventory_agent.draft_pos(None, coverage_days=7)
    assert result.fallback is True
    assert result.model is None
    assert {ln.ingredient_id for d in result.drafts for ln in d.lines} == {1, 2, 3}


async def test_agent_validates_llm_output(monkeypatch, needs):
    async def fake_needs(session, *, coverage_days, today=None):
        return list(needs.values())

    async def fake_llm(**_):
        return (
            batch(
                PODraft(
                    supplier_id=5,
                    lines=[line(999, "5"), line(1, "9999"), line(2, "5"), line(3, "6")],
                    rationale="Weekend rush restock.",
                )
            ),
            "gpt-4o-mini",
        )

    monkeypatch.setattr(inventory_agent, "compute_needs", fake_needs)
    monkeypatch.setattr(inventory_agent, "structured_completion", fake_llm)

    result = await inventory_agent.draft_pos(None, coverage_days=7)
    assert result.fallback is False
    assert result.model == "gpt-4o-mini"
    all_lines = {ln.ingredient_id: ln for d in result.drafts for ln in d.lines}
    assert 999 not in all_lines  # hallucination gone
    assert all_lines[1].qty == Decimal("30")  # capped at 3× deficit
    assert result.violations  # and reported


async def test_agent_empty_needs_short_circuits(monkeypatch):
    async def fake_needs(session, *, coverage_days, today=None):
        return []

    called = False

    async def fake_llm(**_):
        nonlocal called
        called = True

    monkeypatch.setattr(inventory_agent, "compute_needs", fake_needs)
    monkeypatch.setattr(inventory_agent, "structured_completion", fake_llm)

    result = await inventory_agent.draft_pos(None, coverage_days=7)
    assert result.drafts == []
    assert called is False  # no LLM spend when nothing is short

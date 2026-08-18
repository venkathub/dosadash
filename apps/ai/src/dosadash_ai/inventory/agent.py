"""Inventory agent (Phase 6): stock vs forecast → validated draft POs.

Deterministic core, LLM garnish: `compute_needs` decides WHAT is short and
by how much; the LLM pass only rounds quantities to practical purchase
sizes and writes the rationale the owner reads. The guardrail re-anchors
everything to the needs table, so a wrong/malicious/unavailable LLM can
never order outside the forecast deficits — worst case the drafts degrade
to the deterministic fallback (`fallback=True`).

The api worker persists the result as PENDING_APPROVAL purchase orders;
nothing here mutates business state (repo layout: apps/ai reasons,
apps/api owns mutations).
"""

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_ai.inventory.guardrail import (
    deterministic_drafts,
    group_by_supplier,
    sanitize_batch,
)
from dosadash_ai.inventory.needs import compute_needs
from dosadash_ai.llm.client import LLMError, structured_completion
from dosadash_ai.prompts import load_prompt
from dosadash_shared import (
    INVENTORY_PROMPT_VERSION,
    IngredientNeed,
    InventoryDraftResult,
    PODraftBatch,
)

logger = logging.getLogger(__name__)


def _needs_payload(needs: list[IngredientNeed], coverage_days: int) -> str:
    """Compact JSON table the model reasons over (no PII involved)."""
    return json.dumps(
        {
            "coverage_days": coverage_days,
            "ingredients": [
                {
                    "ingredient_id": n.ingredient_id,
                    "name": n.name,
                    "unit": n.unit,
                    "stock": str(n.stock_qty),
                    "reorder_buffer": str(n.reorder_point),
                    "forecast_need": str(n.need_qty),
                    "deficit": str(n.deficit_qty),
                    "supplier": n.supplier_name or "unassigned",
                }
                for n in needs
            ],
        }
    )


async def draft_pos(
    session: AsyncSession, *, coverage_days: int, session_id: str | None = None
) -> InventoryDraftResult:
    needs = await compute_needs(session, coverage_days=coverage_days)
    if not needs:
        return InventoryDraftResult(coverage_days=coverage_days)

    by_id = {n.ingredient_id: n for n in needs}
    messages = [
        {"role": "system", "content": load_prompt(INVENTORY_PROMPT_VERSION)},
        {"role": "user", "content": _needs_payload(needs, coverage_days)},
    ]

    try:
        batch, model = await structured_completion(
            messages=messages,
            response_model=PODraftBatch,
            trace_name="inventory_po",
            prompt_version=INVENTORY_PROMPT_VERSION,
            session_id=session_id,
            max_tokens=1500,
        )
    except LLMError as exc:
        logger.warning("inventory agent LLM pass failed, deterministic fallback: %s", exc)
        return InventoryDraftResult(
            coverage_days=coverage_days,
            needs=needs,
            drafts=deterministic_drafts(by_id),
            fallback=True,
            violations=[f"llm unavailable: {exc}"[:300]],
        )

    lines, rationale, violations = sanitize_batch(batch, by_id)
    return InventoryDraftResult(
        coverage_days=coverage_days,
        needs=needs,
        drafts=group_by_supplier(lines, by_id, rationale=rationale),
        model=model,
        violations=violations,
    )

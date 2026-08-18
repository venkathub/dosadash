"""Inventory draft guardrail (Hard Rule 2 analog): every ingredient the LLM
proposes MUST exist in the deterministic needs table — hallucinated or
out-of-scope ingredients are dropped and recorded as violations, never
ordered. Quantities are clamped to [deficit, MAX_ORDER_FACTOR × deficit]:
the model may round up to practical purchase sizes, but can neither
under-order below the forecast deficit nor hoard.

Grouping is NOT trusted from the model: validated lines are re-grouped by
each ingredient's canonical supplier_id.
"""

from decimal import Decimal

from dosadash_shared import IngredientNeed, PODraft, PODraftBatch, PODraftLine

MAX_ORDER_FACTOR = Decimal("3")


def sanitize_batch(
    batch: PODraftBatch, needs: dict[int, IngredientNeed]
) -> tuple[list[PODraftLine], str, list[str]]:
    """Flatten drafts → validated lines. Returns (lines, rationale, violations)."""
    violations: list[str] = []
    seen: set[int] = set()
    lines: list[PODraftLine] = []

    for draft in batch.drafts:
        for line in draft.lines:
            need = needs.get(line.ingredient_id)
            if need is None:
                violations.append(f"dropped unknown/out-of-scope ingredient {line.ingredient_id}")
                continue
            if line.ingredient_id in seen:
                violations.append(f"dropped duplicate line for ingredient {line.ingredient_id}")
                continue
            seen.add(line.ingredient_id)

            qty = line.qty
            lo, hi = need.deficit_qty, need.deficit_qty * MAX_ORDER_FACTOR
            if qty < lo:
                violations.append(f"raised {need.name} qty {qty} → deficit {lo}")
                qty = lo
            elif qty > hi:
                violations.append(f"capped {need.name} qty {qty} → {hi} (3× deficit)")
                qty = hi
            lines.append(PODraftLine(ingredient_id=line.ingredient_id, qty=qty, reason=line.reason))

    missing = set(needs) - seen
    for ingredient_id in sorted(missing):
        need = needs[ingredient_id]
        violations.append(f"added omitted {need.name} at deficit {need.deficit_qty}")
        lines.append(_deficit_line(need))

    rationale = " ".join(d.rationale for d in batch.drafts if d.rationale).strip()[:1000]
    return lines, rationale or "Restock to cover the demand forecast.", violations


def group_by_supplier(
    lines: list[PODraftLine], needs: dict[int, IngredientNeed], *, rationale: str
) -> list[PODraft]:
    """One PO per canonical supplier (None → unassigned PO)."""
    groups: dict[int | None, list[PODraftLine]] = {}
    for line in lines:
        supplier_id = needs[line.ingredient_id].supplier_id
        groups.setdefault(supplier_id, []).append(line)
    return [
        PODraft(supplier_id=supplier_id, lines=group, rationale=rationale)
        for supplier_id, group in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))
    ]


def _deficit_line(need: IngredientNeed) -> PODraftLine:
    return PODraftLine(
        ingredient_id=need.ingredient_id,
        qty=need.deficit_qty,
        reason=(
            f"stock {need.stock_qty}{need.unit} < forecast need {need.need_qty}{need.unit} "
            f"+ buffer {need.reorder_point}{need.unit}"
        )[:200],
    )


def deterministic_drafts(needs: dict[int, IngredientNeed]) -> list[PODraft]:
    """LLM-free fallback: order exactly the deficits, grouped by supplier."""
    lines = [_deficit_line(need) for need in needs.values()]
    return group_by_supplier(
        lines, needs, rationale="Deterministic restock: forecast deficits over the coverage window."
    )

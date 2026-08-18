"""Deterministic stock-vs-forecast math (the inventory agent's ground truth).

For each ingredient over the coverage window:

    need    = Σ  forecasts.predicted_qty × recipe_ingredients.qty
    deficit = max(need + reorder_point − stock_qty, 0)

Only ingredients with deficit > 0 become candidates. This table is the
agent's ONLY allowed universe — the guardrail drops anything outside it
(Hard Rule 2 analog: no hallucinated ingredients), and the deterministic
fallback orders exactly the deficits when the LLM is unavailable.
"""

from datetime import date, timedelta
from decimal import ROUND_UP, Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_shared import IngredientNeed

_QTY = Decimal("0.001")  # Numeric(12,3) resolution

_NEEDS_SQL = text(
    """
    SELECT i.id            AS ingredient_id,
           i.name          AS name,
           i.unit          AS unit,
           i.stock_qty     AS stock_qty,
           i.reorder_point AS reorder_point,
           i.cost          AS unit_cost,
           i.supplier_id   AS supplier_id,
           s.name          AS supplier_name,
           SUM(f.predicted_qty * ri.qty) AS need_qty
    FROM forecasts f
    JOIN recipe_ingredients ri ON ri.item_id = f.item_id
    JOIN ingredients i         ON i.id = ri.ingredient_id
    LEFT JOIN suppliers s      ON s.id = i.supplier_id
    WHERE f.date >= :start AND f.date < :end
    GROUP BY i.id, i.name, i.unit, i.stock_qty, i.reorder_point,
             i.cost, i.supplier_id, s.name
    ORDER BY i.name
    """
)


async def compute_needs(
    session: AsyncSession, *, coverage_days: int, today: date | None = None
) -> list[IngredientNeed]:
    """Candidate order lines: ingredients whose stock will not cover the
    forecast window plus the reorder buffer."""
    start = today or date.today()
    rows = (
        await session.execute(
            _NEEDS_SQL, {"start": start, "end": start + timedelta(days=coverage_days)}
        )
    ).fetchall()

    needs: list[IngredientNeed] = []
    for row in rows:
        need_qty = Decimal(str(row.need_qty)).quantize(_QTY, rounding=ROUND_UP)
        deficit = need_qty + row.reorder_point - row.stock_qty
        if deficit <= 0:
            continue
        needs.append(
            IngredientNeed(
                ingredient_id=row.ingredient_id,
                name=row.name,
                unit=row.unit,
                stock_qty=row.stock_qty,
                reorder_point=row.reorder_point,
                need_qty=need_qty,
                deficit_qty=deficit.quantize(_QTY, rounding=ROUND_UP),
                unit_cost=row.unit_cost,
                supplier_id=row.supplier_id,
                supplier_name=row.supplier_name,
            )
        )
    return needs

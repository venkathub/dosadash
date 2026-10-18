"""Live inventory drafting from controlled forecasts/recipes, rolled back per case."""

from decimal import Decimal

from _results import evaluate_cases, load_jsonl
from sqlalchemy import text

from dosadash_ai.db import get_sessionmaker
from dosadash_ai.inventory.agent import draft_pos


def score_inventory(response: object, expected: dict[int, tuple[Decimal, int]]) -> dict:
    seen = set()
    bypasses = []
    for draft in response.drafts:
        for line in draft.lines:
            if line.ingredient_id not in expected:
                bypasses.append("unknown ingredient")
                continue
            deficit, supplier = expected[line.ingredient_id]
            if line.ingredient_id in seen:
                bypasses.append("duplicate ingredient")
            seen.add(line.ingredient_id)
            if not deficit <= line.qty <= deficit * 3:
                bypasses.append("quantity outside deficit band")
            if draft.supplier_id != supplier:
                bypasses.append("wrong supplier")
    problems = [] if seen == set(expected) else ["missing required ingredients"]
    # Corrections are safe, but cannot count as evidence of a correct model proposal.
    problems.extend(response.violations)
    return {
        "problems": problems,
        "bypasses": bypasses,
        "model": response.model,
        "fallback": response.fallback,
    }


async def evaluate_live(*, case_timeout: float = 120) -> object:
    async def execute(case):
        async with get_sessionmaker()() as session:
            await session.execute(text("DELETE FROM forecasts"))
            item = await session.scalar(text("SELECT MIN(id) FROM menu_items"))
            await session.execute(
                text("DELETE FROM recipe_ingredients WHERE item_id = :i"), {"i": item}
            )
            expected = {}
            for index, spec in enumerate(case["ingredients"]):
                supplier = await session.scalar(
                    text("INSERT INTO suppliers (name) VALUES (:n) RETURNING id"),
                    {"n": f"Eval {case['id']} supplier {index}"},
                )
                iid = await session.scalar(
                    text(
                        "INSERT INTO ingredients (name, unit, stock_qty, reorder_point, cost, "
                        "is_allergen, supplier_id) VALUES (:n, 'kg', :stock, 0, 10, false, :s) "
                        "RETURNING id"
                    ),
                    {"n": spec["name"], "stock": spec["stock"], "s": supplier},
                )
                await session.execute(
                    text(
                        "INSERT INTO recipe_ingredients (item_id, ingredient_id, qty) "
                        "VALUES (:i, :g, 1)"
                    ),
                    {"i": item, "g": iid},
                )
                deficit = Decimal(str(case["forecast"])) - Decimal(str(spec["stock"]))
                if deficit > 0:
                    expected[iid] = (deficit, supplier)
            await session.execute(
                text(
                    "INSERT INTO forecasts (item_id, date, predicted_qty, model_version) "
                    "VALUES (:i, CURRENT_DATE, :q, 'eval-fixture')"
                ),
                {"i": item, "q": case["forecast"]},
            )
            response = await draft_pos(
                session, coverage_days=1, session_id=f"eval:inventory:{case['id']}"
            )
            if response.fallback or (expected and response.model is None):
                raise RuntimeError("inventory fallback is not live quality evidence")
            return score_inventory(response, expected)

    return await evaluate_cases(
        "inventory", load_jsonl("inventory_live.jsonl"), execute, case_timeout=case_timeout
    )

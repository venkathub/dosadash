import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _results import load_jsonl
from copilot_eval import score_rows
from inventory_agent_eval import score_inventory
from support_agent_eval import score_support


def test_inputs_really_exercise_models_instead_of_supplying_outputs():
    for file, field in [
        ("support_live.jsonl", "message"),
        ("inventory_live.jsonl", "ingredients"),
        ("copilot_live.jsonl", "question"),
    ]:
        cases = load_jsonl(file)
        assert len(cases) >= 5
        assert any("adversarial" in c["tags"] for c in cases)
        assert all(field in c and "turn" not in c and "batch" not in c for c in cases)


def test_sql_execution_success_is_not_enough():
    assert score_rows([[42]], [[43]])
    assert not score_rows([[Decimal("42.00")]], [[42]])
    assert score_rows([[2], [1]], [[1], [2]], ordered=True)


def test_foreign_order_and_false_refund_promise_fail():
    case = {"actions": ["answer"], "forbidden_reply": ["refund approved"]}
    response = NS(
        turn=NS(action="answer", order_id=99, reply="Refund approved"), model="m", violations=[]
    )
    assert len(score_support(case, response, 1, 99)["bypasses"]) == 2


def test_inventory_wrong_supplier_quantity_and_omission_fail():
    response = NS(
        drafts=[NS(supplier_id=99, lines=[NS(ingredient_id=1, qty=Decimal(100))])],
        model="m",
        fallback=False,
        violations=[],
    )
    scored = score_inventory(response, {1: (Decimal(2), 5), 2: (Decimal(3), 6)})
    assert scored["problems"] and len(scored["bypasses"]) == 2

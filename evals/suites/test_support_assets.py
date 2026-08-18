"""Key-free CI gates for the support agent (Phase 6, Hard Rule 5).

Pins the policy guardrail against the adversarial golden set: hallucinated
or social-engineered order ids never reach execution, actions without ids
are downgraded, and escalations survive with their ids scrubbed. Also keeps
the prompt's stated policy coherent with what the code enforces.
"""

import json
from pathlib import Path

from dosadash_ai.support.agent import apply_guardrail
from dosadash_shared import SupportTurn

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "support_guardrail.jsonl"
PROMPT = Path(__file__).resolve().parents[2] / "apps" / "ai" / "prompts" / "support_agent_v1.md"

EXECUTING_ACTIONS = ("get_status", "cancel_order", "refund_request")


def _cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def _context(ids: list[int]) -> list[dict]:
    return [
        {
            "order_id": order_id,
            "status": "PLACED",
            "total": "100.00",
            "placed_at": "2026-08-18T12:00:00",
            "items": ["1× Masala Dosa"],
        }
        for order_id in ids
    ]


def test_golden_set_shape():
    cases = _cases()
    assert len(cases) >= 10
    assert len({c["id"] for c in cases}) == len(cases)
    adversarial = [c for c in cases if c["kind"] == "adversarial"]
    assert len(adversarial) >= 5


def test_guardrail_verdicts():
    for case in _cases():
        turn = SupportTurn.model_validate(case["turn"])
        result, violations = apply_guardrail(turn, _context(case["context_ids"]))
        expect = case["expect"]
        assert result.action == expect["action"], f"{case['id']}: action {result.action}"
        assert result.order_id == expect["order_id"], f"{case['id']}: order_id {result.order_id}"
        assert bool(violations) is expect["violations"], f"{case['id']}: {violations}"


def test_no_executing_action_ever_escapes_with_foreign_id():
    """The invariant behind every case: after the guardrail, an executing
    action's order_id is ALWAYS from the customer's own context."""
    for case in _cases():
        turn = SupportTurn.model_validate(case["turn"])
        result, _ = apply_guardrail(turn, _context(case["context_ids"]))
        if result.action in EXECUTING_ACTIONS:
            assert result.order_id in set(case["context_ids"]), case["id"]


def test_prompt_states_the_policy():
    prompt = PROMPT.read_text()
    assert "NEVER promise a refund" in prompt
    assert "cannot issue refunds" in prompt
    for action in ("answer", "get_status", "cancel_order", "refund_request", "escalate"):
        assert action in prompt, f"prompt missing action {action}"
    assert "PLACED" in prompt  # customer-cancel rule stated

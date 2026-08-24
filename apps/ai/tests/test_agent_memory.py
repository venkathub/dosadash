"""Order-agent memory wiring tests (key-free): STATE payload + prompt v4."""

from pathlib import Path

from dosadash_ai.agent.context import AgentContext, UserMemoryCtx
from dosadash_ai.agent.graph import build_messages
from dosadash_shared import ORDER_AGENT_PROMPT_VERSION, AgentChatRequest

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def _state(memory: UserMemoryCtx | None) -> dict:
    ctx = AgentContext(items={}, memory=memory)
    return {"request": AgentChatRequest(message="my usual"), "ctx": ctx, "knowledge": []}


def test_state_payload_carries_memory():
    memory = UserMemoryCtx(
        usual={"items": [{"item_id": 3, "name": "Masala Dosa", "qty": 2}], "times_ordered": 4},
        recent_orders=("2× Masala Dosa (₹252.00) — 2026-08-15",),
    )
    messages = build_messages(_state(memory))
    state_msg = next(m["content"] for m in messages if m["content"].startswith("STATE: "))
    assert '"memory"' in state_msg
    assert '"times_ordered": 4' in state_msg
    assert "Masala Dosa" in state_msg


def test_anonymous_state_has_null_memory():
    messages = build_messages(_state(None))
    state_msg = next(m["content"] for m in messages if m["content"].startswith("STATE: "))
    assert '"memory": null' in state_msg


def test_current_prompt_states_memory_rules():
    assert ORDER_AGENT_PROMPT_VERSION == "order_agent_v5"
    prompt = (PROMPTS / f"{ORDER_AGENT_PROMPT_VERSION}.md").read_text()
    assert '"memory"' in prompt
    assert "usual" in prompt
    assert "NEVER invent a usual" in prompt
    assert "recent_orders" in prompt
    # cache-stable prefix untouched: memory documented in STATE, not MENU
    assert prompt.index('"memory"') > prompt.index('"MENU:')

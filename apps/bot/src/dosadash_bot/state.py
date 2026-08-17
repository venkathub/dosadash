"""Per-chat conversation state (history + last validated draft).

In-memory and bounded — acceptable for the demo bot (single process,
webhook mode); episodic/long-term memory moves server-side in Phase 6.
The draft stored here is always the GUARDRAIL-VALIDATED draft echoed back
from the agent, never anything the bot computed (Hard Rule 10).
"""

from dataclasses import dataclass, field
from typing import Any

_MAX_HISTORY = 24
_MAX_CHATS = 2000  # simple bound; oldest chats evicted


@dataclass
class ChatState:
    history: list[dict[str, str]] = field(default_factory=list)
    draft: dict[str, Any] | None = None


_states: dict[int, ChatState] = {}


def get_state(chat_id: int) -> ChatState:
    if chat_id not in _states:
        if len(_states) >= _MAX_CHATS:
            _states.pop(next(iter(_states)))
        _states[chat_id] = ChatState()
    return _states[chat_id]


def record_turn(state: ChatState, user_message: str, final: dict[str, Any]) -> None:
    """Append the exchange and adopt the agent's validated draft."""
    state.history.append({"role": "user", "content": user_message})
    state.history.append({"role": "assistant", "content": final.get("reply", "")})
    state.history = state.history[-_MAX_HISTORY:]
    draft = final.get("draft") or {}
    state.draft = draft if draft.get("items") else None


def clear_draft(state: ChatState) -> None:
    state.draft = None


def draft_order_items(state: ChatState) -> list[dict[str, int]]:
    """item_id/qty lines for the place-order call (api re-validates)."""
    if not state.draft:
        return []
    return [{"item_id": i["item_id"], "qty": i["qty"]} for i in state.draft["items"]]


def reset(chat_id: int) -> None:
    _states.pop(chat_id, None)

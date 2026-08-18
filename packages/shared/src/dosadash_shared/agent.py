"""Order agent contracts (Phase 3): structured turns, never free-text (HR 3).

The LLM emits `AgentTurn` (draft items by item_id only); the AI service's
guardrail (Hard Rule 2) validates every item_id against the DB and builds
the authoritative `OrderDraft` (names/prices from the DB, never the model).
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

ORDER_AGENT_PROMPT_VERSION = "order_agent_v4"


class DraftItemIn(BaseModel):
    """One draft line as the LLM proposes it — ids and quantities only."""

    item_id: int
    qty: int = Field(ge=1, le=20)
    notes: str | None = Field(default=None, max_length=200)


class AgentTurn(BaseModel):
    """The LLM's structured turn output. `draft_items` is always the FULL
    current draft (stateless per turn — no diffing ambiguity)."""

    reply: str = Field(min_length=1, max_length=1500)
    draft_items: list[DraftItemIn] = Field(default_factory=list, max_length=20)
    ready_to_place: bool = False


class OrderDraftItem(BaseModel):
    """A validated draft line — name and price come from the DB (HR 2)."""

    item_id: int
    name: str
    qty: int = Field(ge=1, le=20)
    unit_price: Decimal
    notes: str | None = Field(default=None, max_length=200)


class OrderDraft(BaseModel):
    items: list[OrderDraftItem] = Field(default_factory=list, max_length=20)
    subtotal: Decimal = Decimal("0")


class AgentMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class AgentChatRequest(BaseModel):
    """One conversational turn. The caller (web chat / Telegram adapter)
    owns history and echoes back the last validated draft; the agent graph
    itself is stateless per turn (long-term memory arrives in Phase 6)."""

    message: str = Field(min_length=1, max_length=1000)
    history: list[AgentMessage] = Field(default_factory=list, max_length=24)
    draft: OrderDraft | None = None
    user_id: int | None = None  # DB user id for preference lookup
    session_id: str | None = None  # opaque id for Langfuse tracing


class AgentChatResponse(BaseModel):
    reply: str
    draft: OrderDraft
    ready_to_place: bool  # true only when guardrails agree the draft can be placed
    warnings: list[str] = Field(default_factory=list)  # stripped items, allergen conflicts
    kitchen_open: bool
    model: str
    prompt_version: str = ORDER_AGENT_PROMPT_VERSION

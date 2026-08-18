"""Support agent schemas (Phase 6): refund/status help with policy
guardrails and an escalation inbox.

Policy is enforced in code, never trusted from the model:
- the agent can only *reference* orders the customer actually owns
  (DB-checked, Hard Rule 2 analog);
- refunds are NEVER executed by the agent — a refund request becomes an
  escalation with the agent's summary, and a human resolves it (the actual
  refund runs through order_service.refund, admin/owner only);
- cancels go through the order state machine (customers: PLACED only).
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dosadash_shared.agent import AgentMessage
from dosadash_shared.orders import OrderOut

SUPPORT_PROMPT_VERSION = "support_agent_v1"

SupportAction = Literal["answer", "get_status", "cancel_order", "refund_request", "escalate"]


class EscalationStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class SupportTurn(BaseModel):
    """Structured LLM output for one support turn (Hard Rule 3)."""

    reply: str = Field(min_length=1, max_length=1200)
    action: SupportAction = "answer"
    order_id: int | None = None
    reason: str | None = Field(default=None, max_length=300)


class SupportChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[AgentMessage] = Field(default_factory=list, max_length=20)


class SupportAgentRequest(BaseModel):
    """api → ai: the api resolves auth; the ai loads the user's orders."""

    user_id: int
    message: str = Field(min_length=1, max_length=1000)
    history: list[AgentMessage] = Field(default_factory=list, max_length=20)
    session_id: str | None = None


class SupportAgentResponse(BaseModel):
    turn: SupportTurn
    model: str | None = None
    prompt_version: str = SUPPORT_PROMPT_VERSION
    violations: list[str] = []  # guardrail downgrades (e.g. foreign order_id)


class SupportChatOut(BaseModel):
    """What the customer sees after the api executed the (guarded) action."""

    reply: str
    action: SupportAction
    order: OrderOut | None = None
    escalation_id: int | None = None


# ------------------------------------------------------------------- inbox


class EscalationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    order_id: int | None = None
    kind: str
    status: EscalationStatus
    customer_message: str
    agent_summary: str | None = None
    resolved_by: int | None = None
    resolution_note: str | None = None
    created_at: datetime


class EscalationResolveIn(BaseModel):
    note: str = Field(min_length=2, max_length=300)
    refund: bool = False  # True → run the real provider refund on the linked order

"""Customer support chat + admin escalation inbox (Phase 6).

POST /api/v1/support/chat — logged-in customers. The ai decides WHAT to do
(guarded SupportTurn); this router EXECUTES it under the real rules:

- get_status      → order loaded, ownership re-checked here (belt & braces)
- cancel_order    → order_service.transition (customers: PLACED only);
                    refusal degrades to an escalation, never an argument
- refund_request  → escalation row (kind=refund) — the agent NEVER refunds
- escalate        → escalation row (kind=support)

Admin inbox: list/resolve/dismiss. Resolve with refund=true runs the real
provider refund via order_service (admin/owner only, audited).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import CurrentUser, require_role
from dosadash_api.db.models import Escalation, Order, User
from dosadash_api.db.session import get_session
from dosadash_api.routers.orders import _load_order, _order_out, get_payment_provider
from dosadash_api.services import audit, order_service
from dosadash_api.services.ai_client import AIClient, AIServiceError, get_ai_client
from dosadash_shared import (
    EscalationOut,
    EscalationResolveIn,
    EscalationStatus,
    OrderOut,
    OrderState,
    Role,
    SupportAgentRequest,
    SupportChatIn,
    SupportChatOut,
    SupportTurn,
)

router = APIRouter(tags=["support"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminUser = require_role(Role.ADMIN, Role.OWNER)

FALLBACK_ESCALATION_REPLY = (
    "I've passed this to our team — they'll get back to you within 24 hours."
)


async def _own_order(session: AsyncSession, user: User, order_id: int) -> Order | None:
    order = await session.scalar(select(Order).where(Order.id == order_id))
    if order is None or order.user_id != user.id:
        return None  # never leak other customers' orders
    return order


async def _escalate(
    session: AsyncSession, *, user: User, kind: str, message: str, turn: SupportTurn
) -> Escalation:
    escalation = Escalation(
        user_id=user.id,
        order_id=turn.order_id,
        kind=kind,
        customer_message=message[:2000],
        agent_summary=turn.reason,
    )
    session.add(escalation)
    await session.commit()
    return escalation


@router.post("/api/v1/support/chat", response_model=SupportChatOut)
async def support_chat(
    body: SupportChatIn,
    user: CurrentUser,
    session: SessionDep,
    ai: Annotated[AIClient, Depends(get_ai_client)],
) -> SupportChatOut:
    try:
        response = await ai.support_chat(
            SupportAgentRequest(
                user_id=user.id,
                message=body.message,
                history=body.history,
                session_id=f"support:{user.id}",
            )
        )
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail="Support assistant unavailable") from exc

    turn = response.turn
    order_out: OrderOut | None = None
    escalation_id: int | None = None
    reply = turn.reply

    if turn.action == "get_status" and turn.order_id is not None:
        order = await _own_order(session, user, turn.order_id)
        if order is not None:
            order_out = await _order_out(session, await _load_order(session, order.id))

    elif turn.action == "cancel_order" and turn.order_id is not None:
        order = await _own_order(session, user, turn.order_id)
        if order is None:
            reply = "I can't find that order on your account."
        else:
            try:
                await order_service.transition(
                    session, order=order, target=OrderState.CANCELLED, actor=user
                )
                order_out = await _order_out(session, await _load_order(session, order.id))
                reply = f"{turn.reply}\nOrder #{order.id} is cancelled."
            except order_service.OrderError:
                # kitchen already cooking (or race) → human follow-up, no arguing
                escalation = await _escalate(
                    session, user=user, kind="support", message=body.message, turn=turn
                )
                escalation_id = escalation.id
                reply = (
                    f"Order #{order.id} can't be cancelled anymore (the kitchen has "
                    f"started on it). {FALLBACK_ESCALATION_REPLY}"
                )

    elif turn.action == "refund_request":
        escalation = await _escalate(
            session, user=user, kind="refund", message=body.message, turn=turn
        )
        escalation_id = escalation.id

    elif turn.action == "escalate":
        escalation = await _escalate(
            session, user=user, kind="support", message=body.message, turn=turn
        )
        escalation_id = escalation.id

    return SupportChatOut(
        reply=reply, action=turn.action, order=order_out, escalation_id=escalation_id
    )


# --------------------------------------------------------------- admin inbox


@router.get("/api/v1/admin/escalations", response_model=list[EscalationOut])
async def list_escalations(
    session: SessionDep,
    admin: User = AdminUser,
    status: EscalationStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[EscalationOut]:
    stmt = (
        select(Escalation).order_by(Escalation.created_at.desc(), Escalation.id.desc()).limit(limit)
    )
    if status is not None:
        stmt = stmt.where(Escalation.status == status)
    return [EscalationOut.model_validate(e) for e in (await session.scalars(stmt)).all()]


async def _open_escalation(session: AsyncSession, escalation_id: int) -> Escalation:
    escalation = await session.get(Escalation, escalation_id)
    if escalation is None:
        raise HTTPException(status_code=404, detail="Escalation not found")
    if escalation.status != EscalationStatus.OPEN:
        raise HTTPException(status_code=409, detail=f"Escalation already {escalation.status.value}")
    return escalation


@router.post("/api/v1/admin/escalations/{escalation_id}/resolve", response_model=EscalationOut)
async def resolve_escalation(
    escalation_id: int,
    body: EscalationResolveIn,
    session: SessionDep,
    provider: Annotated[object, Depends(get_payment_provider)],
    admin: User = AdminUser,
) -> EscalationOut:
    escalation = await _open_escalation(session, escalation_id)

    if body.refund:
        if escalation.order_id is None:
            raise HTTPException(status_code=422, detail="Escalation has no linked order to refund")
        order = await session.get(Order, escalation.order_id)
        try:
            await order_service.refund(
                session,
                order=order,
                actor=admin,
                provider=provider,  # type: ignore[arg-type]
                reason=f"escalation #{escalation.id}: {body.note}",
            )
        except order_service.OrderError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    escalation.status = EscalationStatus.RESOLVED
    escalation.resolved_by = admin.id
    escalation.resolution_note = body.note
    audit.record(
        session,
        actor=admin,
        action="escalation.resolve",
        entity=f"escalation:{escalation.id}",
        detail={"refund": body.refund, "note": body.note},
    )
    await session.commit()
    return EscalationOut.model_validate(escalation)


@router.post("/api/v1/admin/escalations/{escalation_id}/dismiss", response_model=EscalationOut)
async def dismiss_escalation(
    escalation_id: int,
    body: EscalationResolveIn,
    session: SessionDep,
    admin: User = AdminUser,
) -> EscalationOut:
    escalation = await _open_escalation(session, escalation_id)
    escalation.status = EscalationStatus.DISMISSED
    escalation.resolved_by = admin.id
    escalation.resolution_note = body.note
    audit.record(
        session,
        actor=admin,
        action="escalation.dismiss",
        entity=f"escalation:{escalation.id}",
        detail={"note": body.note},
    )
    await session.commit()
    return EscalationOut.model_validate(escalation)

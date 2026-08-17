"""Customer chat endpoints — thin proxy to the AI order agent.

The api owns auth (JWT → user_id) and network exposure; ALL reasoning
lives in apps/ai (same graph serves web and Telegram — docs/05). Anonymous
chat is allowed (browse/ask); placing an order still requires login via the
normal checkout flow, which re-validates everything server-side.
"""

import secrets
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import jwt as pyjwt
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.security import decode_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_api.db.session import get_session
from dosadash_api.providers import PaymentProvider
from dosadash_api.routers import orders as orders_router
from dosadash_api.routers.orders import get_payment_provider
from dosadash_api.services import order_service
from dosadash_shared import (
    AgentChatRequest,
    AgentChatResponse,
    AgentMessage,
    ChannelType,
    OrderDraft,
    OrderItemIn,
    OrderOut,
)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

_bearer = HTTPBearer(auto_error=False)


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User | None:
    """Logged-in user when a valid token is presented; None when absent.
    A *present but invalid* token is still a 401 (never silently anonymous)."""
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials, get_settings().jwt_secret)
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc
    return await session.get(User, int(payload["sub"]))


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


class AgentGateway:
    """api → ai agent calls (X-Internal-Token, mirrors AIClient). Injectable
    so tests substitute a fake without touching the network."""

    def __init__(self, base_url: str, internal_token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Internal-Token": internal_token}

    async def chat(self, request: AgentChatRequest) -> AgentChatResponse:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/agent/chat",
                    json=request.model_dump(mode="json"),
                    headers=self._headers,
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Assistant unavailable") from exc
        return AgentChatResponse.model_validate(resp.json())

    async def stream(self, request: AgentChatRequest) -> AsyncIterator[bytes]:
        """Pass the AI service's SSE bytes through untouched."""
        try:
            async with (
                httpx.AsyncClient(timeout=120) as client,
                client.stream(
                    "POST",
                    f"{self._base_url}/internal/agent/chat/stream",
                    json=request.model_dump(mode="json"),
                    headers=self._headers,
                ) as resp,
            ):
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    yield chunk
        except httpx.HTTPError:
            # Mid-stream failure: emit a terminal SSE error event instead of
            # silently truncating (headers may already be sent).
            yield b'data: {"type": "error", "detail": "Assistant unavailable"}\n\n'


def get_agent_gateway() -> AgentGateway:
    s = get_settings()
    return AgentGateway(base_url=s.ai_base_url, internal_token=s.internal_api_token)


GatewayDep = Annotated[AgentGateway, Depends(get_agent_gateway)]


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[AgentMessage] = Field(default_factory=list, max_length=24)
    draft: OrderDraft | None = None


def _agent_request(body: ChatIn, user: User | None) -> AgentChatRequest:
    return AgentChatRequest(
        message=body.message,
        history=body.history,
        draft=body.draft,
        user_id=user.id if user else None,
        session_id=f"web:{user.id}" if user else "web:anon",
    )


@router.post("", response_model=AgentChatResponse)
async def chat(body: ChatIn, user: OptionalUser, gateway: GatewayDep) -> AgentChatResponse:
    return await gateway.chat(_agent_request(body, user))


@router.post("/stream")
async def chat_stream(body: ChatIn, user: OptionalUser, gateway: GatewayDep) -> StreamingResponse:
    return StreamingResponse(
        gateway.stream(_agent_request(body, user)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------- telegram (bot → api)
# The bot is an adapter (Hard Rule 10): it authenticates with the shared
# internal token and identifies the human by tg_user_id; the api resolves
# the linked account. Chat works unlinked (no prefs); placing requires a
# linked account and goes through order_service like any checkout.


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


async def _tg_user(session: AsyncSession, tg_user_id: int) -> User | None:
    return await session.scalar(select(User).where(User.tg_user_id == tg_user_id))


class TelegramChatIn(ChatIn):
    tg_user_id: int


class TelegramPlaceIn(BaseModel):
    tg_user_id: int
    items: list[OrderItemIn] = Field(min_length=1, max_length=20)


@router.post("/telegram/stream")
async def telegram_chat_stream(
    body: TelegramChatIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    gateway: GatewayDep,
    x_internal_token: Annotated[str, Header()] = "",
) -> StreamingResponse:
    _check_internal_token(x_internal_token)
    user = await _tg_user(session, body.tg_user_id)
    request = AgentChatRequest(
        message=body.message,
        history=body.history,
        draft=body.draft,
        user_id=user.id if user else None,
        session_id=f"tg:{body.tg_user_id}",
    )
    return StreamingResponse(
        gateway.stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/telegram/place", response_model=OrderOut, status_code=201)
async def telegram_place(
    body: TelegramPlaceIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    provider: Annotated[PaymentProvider, Depends(get_payment_provider)],
    x_internal_token: Annotated[str, Header()] = "",
) -> OrderOut:
    """Place the agent's confirmed draft as a real order — same
    order_service path as web checkout (state machine, availability,
    hours all re-validated)."""
    _check_internal_token(x_internal_token)
    user = await _tg_user(session, body.tg_user_id)
    if user is None:
        raise HTTPException(status_code=403, detail="Telegram account not linked")
    try:
        order = await order_service.create_order(
            session, user=user, items_in=body.items, provider=provider, channel=ChannelType.TELEGRAM
        )
    except order_service.ItemsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except order_service.ItemsUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (order_service.KitchenPaused, order_service.OutsideBusinessHours) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    loaded = await orders_router._load_order(session, order.id)
    return await orders_router._order_out(session, loaded)

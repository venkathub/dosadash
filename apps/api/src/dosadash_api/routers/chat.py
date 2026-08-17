"""Customer chat endpoints — thin proxy to the AI order agent.

The api owns auth (JWT → user_id) and network exposure; ALL reasoning
lives in apps/ai (same graph serves web and Telegram — docs/05). Anonymous
chat is allowed (browse/ask); placing an order still requires login via the
normal checkout flow, which re-validates everything server-side.
"""

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.security import decode_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import User
from dosadash_api.db.session import get_session
from dosadash_shared import AgentChatRequest, AgentChatResponse, AgentMessage, OrderDraft

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

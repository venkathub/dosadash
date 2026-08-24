"""Thin API client for bot→api internal calls (Hard Rule 10: no business
logic here — the bot just forwards and renders)."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


def parse_sse(buffer: str, chunk: str) -> tuple[list[dict[str, Any]], str]:
    """Feed a text chunk into an SSE buffer → (complete events, remainder)."""
    buffer += chunk
    frames = buffer.split("\n\n")
    remainder = frames.pop()
    events = []
    for frame in frames:
        if frame.startswith("data: "):
            try:
                events.append(json.loads(frame[6:]))
            except ValueError:
                continue  # malformed frame — skip, the final event is the contract
    return events, remainder


async def stream_chat(
    *,
    api_base_url: str,
    internal_token: str,
    tg_user_id: int,
    message: str,
    history: list[dict[str, str]],
    draft: dict[str, Any] | None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield agent events (delta/final/error) from the api's telegram SSE proxy."""
    payload = {"tg_user_id": tg_user_id, "message": message, "history": history, "draft": draft}
    try:
        async with (
            httpx.AsyncClient(timeout=120) as client,
            client.stream(
                "POST",
                f"{api_base_url}/api/v1/chat/telegram/stream",
                json=payload,
                headers={"X-Internal-Token": internal_token},
            ) as resp,
        ):
            if resp.status_code != 200:
                yield {"type": "error", "detail": f"HTTP {resp.status_code}"}
                return
            buffer = ""
            async for chunk in resp.aiter_text():
                events, buffer = parse_sse(buffer, chunk)
                for event in events:
                    yield event
    except httpx.HTTPError:
        yield {"type": "error", "detail": "API unreachable"}


class SttClientResult:
    def __init__(
        self,
        ok: bool,
        transcript: str | None = None,
        language: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.ok = ok
        self.transcript = transcript
        self.language = language
        self.detail = detail


async def transcribe_voice(
    *,
    api_base_url: str,
    internal_token: str,
    tg_user_id: int,
    audio_base64: str,
    mime_type: str,
) -> SttClientResult:
    """Voice note → PII-redacted transcript via the api's STT proxy (Phase 7).
    The bot never talks to a speech provider itself (Hard Rule 10)."""
    payload = {"tg_user_id": tg_user_id, "audio_base64": audio_base64, "mime_type": mime_type}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/chat/telegram/stt",
                json=payload,
                headers={"X-Internal-Token": internal_token},
            )
    except httpx.HTTPError:
        return SttClientResult(ok=False, detail="API unreachable")
    if resp.status_code != 200:
        return SttClientResult(ok=False, detail=f"HTTP {resp.status_code}")
    data = resp.json()
    return SttClientResult(
        ok=True, transcript=data.get("transcript", ""), language=data.get("language")
    )


class PlaceResult:
    def __init__(
        self,
        ok: bool,
        order_id: int | None = None,
        total: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.ok = ok
        self.order_id = order_id
        self.total = total
        self.detail = detail


async def place_order(
    *,
    api_base_url: str,
    internal_token: str,
    tg_user_id: int,
    items: list[dict[str, int]],
) -> PlaceResult:
    """Place a confirmed draft via the api (order_service re-validates)."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/chat/telegram/place",
                json={"tg_user_id": tg_user_id, "items": items},
                headers={"X-Internal-Token": internal_token},
            )
    except httpx.HTTPError:
        return PlaceResult(ok=False, detail="API unreachable")
    if resp.status_code == 201:
        data = resp.json()
        return PlaceResult(ok=True, order_id=data["id"], total=data["total"])
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    return PlaceResult(ok=False, detail=detail)


class PODecisionResult:
    def __init__(self, ok: bool, status: str | None = None, detail: str | None = None) -> None:
        self.ok = ok
        self.status = status
        self.detail = detail


async def po_decision(
    *,
    api_base_url: str,
    internal_token: str,
    tg_user_id: int,
    po_id: int,
    action: str,
) -> PODecisionResult:
    """Forward an owner's Approve/Reject tap; the api re-checks RBAC + state."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/internal/po/decision",
                json={"tg_user_id": tg_user_id, "po_id": po_id, "action": action},
                headers={"X-Internal-Token": internal_token},
            )
    except httpx.HTTPError:
        return PODecisionResult(ok=False, detail="API unreachable")
    if resp.status_code != 200:
        return PODecisionResult(ok=False, detail=f"HTTP {resp.status_code}")
    data = resp.json()
    return PODecisionResult(
        ok=bool(data.get("ok")), status=data.get("status"), detail=data.get("detail")
    )


class LinkResult:
    def __init__(self, ok: bool, name: str | None = None, detail: str | None = None) -> None:
        self.ok = ok
        self.name = name
        self.detail = detail


async def link_account(
    *,
    api_base_url: str,
    internal_token: str,
    code: str,
    tg_user_id: int,
    tg_name: str | None,
    client: httpx.AsyncClient | None = None,
) -> LinkResult:
    url = f"{api_base_url}/api/v1/auth/telegram/link"
    payload = {"code": code, "tg_user_id": tg_user_id, "tg_name": tg_name}
    headers = {"X-Internal-Token": internal_token}
    try:
        if client is not None:
            resp = await client.post(url, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=10) as c:
                resp = await c.post(url, json=payload, headers=headers)
    except httpx.HTTPError:
        return LinkResult(ok=False, detail="API unreachable")
    if resp.status_code == 200:
        return LinkResult(ok=True, name=resp.json().get("name"))
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    return LinkResult(ok=False, detail=detail)


class FeedbackDecisionResult:
    def __init__(self, ok: bool, status: str | None = None, detail: str | None = None) -> None:
        self.ok = ok
        self.status = status
        self.detail = detail


async def feedback_decision(
    *,
    api_base_url: str,
    internal_token: str,
    tg_user_id: int,
    report_id: int,
    action: str,
) -> FeedbackDecisionResult:
    """Forward an admin's Approve/Reject tap; the api re-checks RBAC + state."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{api_base_url}/api/v1/internal/feedback/decision",
                json={"tg_user_id": tg_user_id, "report_id": report_id, "action": action},
                headers={"X-Internal-Token": internal_token},
            )
    except httpx.HTTPError:
        return FeedbackDecisionResult(ok=False, detail="API unreachable")
    if resp.status_code != 200:
        return FeedbackDecisionResult(ok=False, detail=f"HTTP {resp.status_code}")
    data = resp.json()
    return FeedbackDecisionResult(
        ok=bool(data.get("ok")), status=data.get("status"), detail=data.get("detail")
    )

"""WebSocket endpoints.

/ws/kds          — kitchen display: snapshot of active orders + live events (staff JWT)
/ws/orders/{id}  — customer live tracking for one order (owner or staff JWT)
/ws/fixer        — self-healing-loop live feed for the /fixer portal (admin/owner JWT)

Browsers can't set headers on WebSocket upgrade → JWT arrives as ?token=.
Close codes: 4401 bad/missing token, 4403 insufficient role/ownership.
"""

import asyncio
import contextlib
import json
import logging

import jwt as pyjwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from dosadash_api.auth.security import decode_access_token
from dosadash_api.config import get_settings
from dosadash_api.db.models import Order, OrderItem, User
from dosadash_api.db.session import get_sessionmaker
from dosadash_api.events import FEEDBACK_CHANNEL, ORDERS_CHANNEL, get_redis, order_event_payload
from dosadash_api.services.order_service import STAFF_ROLES
from dosadash_shared import OrderState, Role

logger = logging.getLogger(__name__)
router = APIRouter()

ACTIVE_STATES = (
    OrderState.PLACED,
    OrderState.CONFIRMED,
    OrderState.COOKING,
    OrderState.READY,
)


async def _authenticate(token: str) -> User | None:
    try:
        payload = decode_access_token(token, get_settings().jwt_secret)
    except pyjwt.PyJWTError:
        return None
    async with get_sessionmaker()() as session:
        return await session.get(User, int(payload["sub"]))


async def _forward_events(
    websocket: WebSocket, *, order_id: int | None = None, channel: str = ORDERS_CHANNEL
) -> None:
    """Relay one pubsub channel to this socket (optionally filtered to one order)."""
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            if order_id is not None:
                event = json.loads(message["data"])
                if event.get("order_id") != order_id:
                    continue
            await websocket.send_text(message["data"])
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()


async def _run_until_disconnect(websocket: WebSocket, forward: asyncio.Task) -> None:
    """Keep the socket open until the client goes away, then stop forwarding."""
    try:
        while True:
            await websocket.receive_text()  # ignore inbound (ping/keepalive)
    except WebSocketDisconnect:
        pass
    finally:
        forward.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await forward


@router.websocket("/ws/kds")
async def kds_socket(websocket: WebSocket, token: str = Query(default="")) -> None:
    user = await _authenticate(token)
    if user is None:
        await websocket.close(code=4401)
        return
    if user.role not in STAFF_ROLES:
        await websocket.close(code=4403)
        return
    await websocket.accept()

    async with get_sessionmaker()() as session:
        orders = (
            await session.scalars(
                select(Order)
                .where(Order.status.in_(ACTIVE_STATES))
                .options(selectinload(Order.items).selectinload(OrderItem.item))
                .order_by(Order.placed_at)
            )
        ).all()
    await websocket.send_text(
        json.dumps(
            {
                "type": "snapshot",
                "orders": [order_event_payload("order.snapshot", o) for o in orders],
            }
        )
    )
    forward = asyncio.create_task(_forward_events(websocket))
    await _run_until_disconnect(websocket, forward)


@router.websocket("/ws/fixer")
async def fixer_socket(websocket: WebSocket, token: str = Query(default="")) -> None:
    """Phase 14: the /fixer portal's live feed — relays pubsub:feedback
    (every lifecycle stage publishes there). Deliberately event-only, no
    snapshot: the portal fetches state via the admin REST endpoints and
    uses events as its refresh trigger, so socket and REST can never tell
    different stories."""
    user = await _authenticate(token)
    if user is None:
        await websocket.close(code=4401)
        return
    if user.role not in (Role.ADMIN, Role.OWNER):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    await websocket.send_text(json.dumps({"type": "feedback.hello"}))
    forward = asyncio.create_task(_forward_events(websocket, channel=FEEDBACK_CHANNEL))
    await _run_until_disconnect(websocket, forward)


@router.websocket("/ws/orders/{order_id}")
async def order_tracking_socket(
    websocket: WebSocket, order_id: int, token: str = Query(default="")
) -> None:
    user = await _authenticate(token)
    if user is None:
        await websocket.close(code=4401)
        return
    async with get_sessionmaker()() as session:
        order = await session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items).selectinload(OrderItem.item))
        )
    if order is None or (order.user_id != user.id and user.role not in STAFF_ROLES):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    await websocket.send_text(json.dumps(order_event_payload("order.snapshot", order)))
    forward = asyncio.create_task(_forward_events(websocket, order_id=order_id))
    await _run_until_disconnect(websocket, forward)

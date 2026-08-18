"""Internal endpoints backing the MCP server (Phase 6).

The MCP server is a thin stdio adapter (runs wherever Claude Desktop runs);
these endpoints keep ALL business rules server-side:

- GET  /api/v1/internal/mcp/inventory — ingredient stock snapshot
- POST /api/v1/internal/mcp/place     — place an order as the MCP demo
  user via order_service (item validation = Hard Rule 2, state machine,
  hours/pause enforcement all re-run here)

X-Internal-Token guarded, same trust boundary as bot→api.
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import Ingredient, User
from dosadash_api.db.session import get_session
from dosadash_api.providers import PaymentProvider
from dosadash_api.routers import orders as orders_router
from dosadash_api.routers.orders import get_payment_provider
from dosadash_api.services import order_service
from dosadash_shared import ChannelType, OrderItemIn, OrderOut, Role

router = APIRouter(prefix="/api/v1/internal/mcp", tags=["internal:mcp"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

MCP_DEMO_PHONE = "+919000000099"  # dedicated demo identity, role stays customer


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


class InventoryRow(BaseModel):
    ingredient_id: int
    name: str
    unit: str
    stock_qty: str
    reorder_point: str
    low: bool


@router.get("/inventory", response_model=list[InventoryRow])
async def inventory(
    session: SessionDep, x_internal_token: Annotated[str, Header()] = ""
) -> list[InventoryRow]:
    _check_internal_token(x_internal_token)
    rows = (await session.scalars(select(Ingredient).order_by(Ingredient.name))).all()
    return [
        InventoryRow(
            ingredient_id=i.id,
            name=i.name,
            unit=i.unit,
            stock_qty=str(i.stock_qty),
            reorder_point=str(i.reorder_point),
            low=i.stock_qty <= i.reorder_point,
        )
        for i in rows
    ]


class McpPlaceIn(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1, max_length=20)


@router.post("/place", response_model=OrderOut, status_code=201)
async def place(
    body: McpPlaceIn,
    session: SessionDep,
    provider: Annotated[PaymentProvider, Depends(get_payment_provider)],
    x_internal_token: Annotated[str, Header()] = "",
) -> OrderOut:
    _check_internal_token(x_internal_token)
    user = await session.scalar(select(User).where(User.phone == MCP_DEMO_PHONE))
    if user is None:
        user = User(phone=MCP_DEMO_PHONE, name="Claude (MCP demo)", role=Role.CUSTOMER)
        session.add(user)
        await session.commit()
    try:
        order = await order_service.create_order(
            session, user=user, items_in=body.items, provider=provider, channel=ChannelType.WEB
        )
    except order_service.ItemsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except order_service.ItemsUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (order_service.KitchenPaused, order_service.OutsideBusinessHours) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    loaded = await orders_router._load_order(session, order.id)
    return await orders_router._order_out(session, loaded)

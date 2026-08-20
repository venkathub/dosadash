"""Customer reviews (Phase 8): one review per DELIVERED order.

Trust model: the customer writes only rating + text. Scoring internals
(sentiment, aspects, AI reply drafts) are backoffice-only — `ReviewOut`
deliberately excludes them. Ownership is re-checked on every route: an order
you don't own is a 404 (existence is not leaked), mirroring the support
agent's convention.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import CurrentUser
from dosadash_api.db.models import Order, Review
from dosadash_api.db.session import get_session
from dosadash_shared import OrderState, ReviewCreateIn, ReviewOut

router = APIRouter(prefix="/api/v1/orders", tags=["reviews"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _owned_order(session: AsyncSession, order_id: int, user_id: int) -> Order:
    order = await session.scalar(
        select(Order).where(Order.id == order_id, Order.user_id == user_id)
    )
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@router.post("/{order_id}/review", response_model=ReviewOut, status_code=201)
async def create_review(
    order_id: int, body: ReviewCreateIn, user: CurrentUser, session: SessionDep
) -> ReviewOut:
    order = await _owned_order(session, order_id, user.id)
    if order.status != OrderState.DELIVERED:
        raise HTTPException(status_code=409, detail="only delivered orders can be reviewed")
    existing = await session.scalar(select(Review.id).where(Review.order_id == order_id))
    if existing is not None:
        raise HTTPException(status_code=409, detail="this order already has a review")
    review = Review(order_id=order_id, user_id=user.id, rating=body.rating, text=body.text.strip())
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return ReviewOut.model_validate(review)


@router.get("/{order_id}/review", response_model=ReviewOut)
async def get_review(order_id: int, user: CurrentUser, session: SessionDep) -> ReviewOut:
    await _owned_order(session, order_id, user.id)
    review = await session.scalar(select(Review).where(Review.order_id == order_id))
    if review is None:
        raise HTTPException(status_code=404, detail="no review for this order")
    return ReviewOut.model_validate(review)

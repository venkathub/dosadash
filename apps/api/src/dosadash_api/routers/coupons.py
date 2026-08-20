"""Customer coupon endpoints (Phase 7).

POST /api/v1/coupons/preview — price a cart with a coupon BEFORE checkout.
Auth required (per-user limits need to know who's asking). The preview is
advisory: checkout re-resolves the coupon from scratch (never trust the
client's arithmetic — same principle as order totals).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import CurrentUser
from dosadash_api.db.session import get_session
from dosadash_api.services import coupon_service, order_service
from dosadash_shared import CouponPreviewIn, CouponPreviewOut

router = APIRouter(prefix="/api/v1/coupons", tags=["coupons"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/preview", response_model=CouponPreviewOut)
async def preview(
    body: CouponPreviewIn, user: CurrentUser, session: SessionDep
) -> CouponPreviewOut:
    try:
        wanted, _, found = await order_service._validate_items(session, body.items)
    except order_service.ItemsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except order_service.ItemsUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    subtotal, gst, _ = order_service._totals(wanted, found)
    try:
        coupon, discount = await coupon_service.resolve(
            session, code=body.code, user_id=user.id, subtotal=subtotal
        )
    except coupon_service.CouponError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    gst = coupon_service.discounted_gst(gst, subtotal, discount)
    return CouponPreviewOut(
        code=coupon.code,
        description=coupon.description,
        subtotal=subtotal,
        discount=discount,
        gst=gst,
        total=subtotal - discount + gst,
    )

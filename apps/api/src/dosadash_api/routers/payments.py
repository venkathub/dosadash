"""Payment endpoints.

GET  /api/v1/payments/config            — provider + public key for checkout JS
POST /api/v1/payments/razorpay/webhook  — signature-verified event sink
                                          (source of truth for capture status)
"""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import Settings, get_settings
from dosadash_api.db.models import Payment
from dosadash_api.db.session import get_session
from dosadash_api.providers import verify_webhook_signature
from dosadash_api.routers.orders import get_payment_provider
from dosadash_shared import PaymentStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


class PaymentConfig(BaseModel):
    provider: str
    key_id: str | None = None


@router.get("/config", response_model=PaymentConfig)
async def payment_config(settings: SettingsDep) -> PaymentConfig:
    provider = get_payment_provider()
    key_id = settings.razorpay_key_id if provider.name == "razorpay" else None
    return PaymentConfig(provider=provider.name, key_id=key_id)


async def _mark_payment(
    session: AsyncSession,
    provider_order_id: str,
    status: PaymentStatus,
    *,
    verified: bool,
    provider_payment_id: str | None = None,
) -> bool:
    payment = await session.scalar(
        select(Payment).where(Payment.provider_order_id == provider_order_id)
    )
    if payment is None:
        return False
    payment.status = status
    if provider_payment_id:
        payment.provider_payment_id = provider_payment_id  # enables admin refunds
    if verified:
        payment.signature_verified = True
    await session.commit()
    return True


async def _mark_refunded(session: AsyncSession, provider_payment_id: str, refund_id: str) -> bool:
    """Reconcile refunds initiated outside the app (Razorpay dashboard)."""
    payment = await session.scalar(
        select(Payment).where(Payment.provider_payment_id == provider_payment_id)
    )
    if payment is None:
        return False
    payment.status = PaymentStatus.REFUNDED
    payment.refund_id = refund_id
    await session.commit()
    return True


@router.post("/razorpay/webhook")
async def razorpay_webhook(
    request: Request, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    if not settings.razorpay_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook not configured")
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_webhook_signature(
        body=body, signature=signature, webhook_secret=settings.razorpay_webhook_secret
    ):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event = json.loads(body)
    kind = event.get("event", "")
    handled = False
    if kind in ("payment.captured", "payment.failed"):
        entity = event["payload"]["payment"]["entity"]
        captured = kind == "payment.captured"
        status = PaymentStatus.CAPTURED if captured else PaymentStatus.FAILED
        handled = await _mark_payment(
            session,
            entity.get("order_id", ""),
            status,
            verified=captured,
            provider_payment_id=entity.get("id") if captured else None,
        )
    elif kind == "refund.processed":
        entity = event["payload"]["refund"]["entity"]
        handled = await _mark_refunded(session, entity.get("payment_id", ""), entity.get("id", ""))
        if not handled:
            logger.warning("refund.processed for unknown payment %s", entity.get("payment_id"))

    if not handled:
        logger.info("razorpay webhook ignored: event=%s", kind)
    return {"ok": True, "handled": handled}

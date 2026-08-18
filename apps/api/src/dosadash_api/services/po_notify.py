"""Owner Telegram notification for freshly drafted POs (api → bot).

Best-effort by design: a bot outage must never fail the nightly task or the
admin draft-now flow — owners still see drafts in the backoffice Inventory
tab. The bot renders the message + Approve/Reject buttons; decisions come
back through /api/v1/internal/po/decision with RBAC re-checked server-side.
"""

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.config import get_settings
from dosadash_api.db.models import PurchaseOrder, User
from dosadash_api.services import po_service
from dosadash_shared import Role

logger = logging.getLogger(__name__)


async def _recipients(session: AsyncSession) -> list[int]:
    """tg_user_ids of linked admin/owner accounts."""
    rows = await session.scalars(
        select(User.tg_user_id).where(
            User.role.in_([Role.ADMIN, Role.OWNER]), User.tg_user_id.is_not(None)
        )
    )
    return list(rows.all())


def _summary(po: PurchaseOrder) -> dict:
    return {
        "po_id": po.id,
        "supplier_name": po.supplier.name if po.supplier else None,
        "lines": [
            {"name": item.ingredient.name, "qty": str(item.qty), "unit": item.unit}
            for item in po.items
        ],
        "expected_cost": str(po.expected_cost) if po.expected_cost is not None else None,
        "rationale": po.rationale,
    }


async def notify_owners_po_drafted(session: AsyncSession, po_ids: list[int]) -> int:
    """Send one approval card per PO to every linked admin/owner. Returns the
    number of messages attempted (0 when unconfigured/no recipients)."""
    if not po_ids:
        return 0
    settings = get_settings()
    if not settings.bot_base_url or not settings.internal_api_token:
        return 0
    recipients = await _recipients(session)
    if not recipients:
        return 0

    sent = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for po_id in po_ids:
            po = await po_service.get_po(session, po_id)
            if po is None:
                continue
            payload = _summary(po)
            for tg_user_id in recipients:
                try:
                    resp = await client.post(
                        f"{settings.bot_base_url.rstrip('/')}/internal/po-notify",
                        json={"tg_user_id": tg_user_id, **payload},
                        headers={"X-Internal-Token": settings.internal_api_token},
                    )
                    resp.raise_for_status()
                    sent += 1
                except httpx.HTTPError:  # best-effort by design
                    logger.warning(
                        "po notify failed (po %s → tg %s)", po_id, tg_user_id, exc_info=True
                    )
    return sent

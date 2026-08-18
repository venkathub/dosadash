"""Admin CRM (Phase 5, docs/04 O6): segment summary + win-back targets.

Data comes from `customer_segments`, refreshed nightly by the 03:00 IST
Celery job. Phones are shown to admins (business need, RBAC-guarded) —
Hard Rule 8 (PII redaction) applies to LLM calls and logs, and this data
never reaches either.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import require_role
from dosadash_api.db.models import User
from dosadash_api.db.session import get_session
from dosadash_shared import CrmReport, CrmTierSummary, CrmUserRow, Role

router = APIRouter(prefix="/api/v1/admin/crm", tags=["admin:crm"])

AdminUser = require_role(Role.ADMIN, Role.OWNER)


@router.get("/segments", response_model=CrmReport)
async def crm_segments(
    session: Annotated[AsyncSession, Depends(get_session)],
    at_risk_limit: Annotated[int, Query(ge=1, le=100)] = 25,
    admin: User = AdminUser,
) -> CrmReport:
    computed_at = await session.scalar(text("SELECT MAX(computed_at) FROM customer_segments"))
    tier_rows = (
        await session.execute(
            text(
                """
                SELECT rfm_tier, COUNT(*) AS users,
                       AVG(churn_risk) AS avg_churn, SUM(ltv) AS total_ltv
                FROM customer_segments
                GROUP BY rfm_tier ORDER BY total_ltv DESC
                """
            )
        )
    ).fetchall()
    # Win-back shortlist: substantial spenders most likely to be slipping away.
    at_risk_rows = (
        await session.execute(
            text(
                """
                SELECT s.user_id, u.name, u.phone, s.rfm_tier, s.churn_risk, s.ltv
                FROM customer_segments s JOIN users u ON u.id = s.user_id
                WHERE s.rfm_tier IN ('AT_RISK', 'LOST') OR s.churn_risk >= 0.6
                ORDER BY s.ltv * s.churn_risk DESC
                LIMIT :limit
                """
            ),
            {"limit": at_risk_limit},
        )
    ).fetchall()
    return CrmReport(
        computed_at=computed_at,
        tiers=[
            CrmTierSummary(
                tier=r.rfm_tier,
                users=int(r.users),
                avg_churn_risk=round(float(r.avg_churn), 3),
                total_ltv=round(float(r.total_ltv), 2),
            )
            for r in tier_rows
        ],
        at_risk=[
            CrmUserRow(
                user_id=r.user_id,
                name=r.name,
                phone=r.phone,
                rfm_tier=r.rfm_tier,
                churn_risk=float(r.churn_risk),
                ltv=float(r.ltv),
            )
            for r in at_risk_rows
        ],
    )

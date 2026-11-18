"""Durable provider admission limits. Unknown charges retain their reservation.

This is an application circuit breaker, not a provider billing hard cap: an actual
charge can exceed its configured estimate. No prompts, identities or keys are stored.
"""

import asyncio
import logging
import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any
from uuid import uuid4

import litellm
from sqlalchemy import text

from dosadash_ai.config import get_settings
from dosadash_ai.db import get_sessionmaker
from dosadash_ai.llm.errors import LLMError

logger = logging.getLogger(__name__)


class ProviderBudgetExceeded(LLMError):
    """Admission denied; never retry against another provider to bypass the limit."""


@dataclass(frozen=True)
class Reservation:
    id: str


async def reserve(operation: str, *, units: int = 1) -> Reservation | None:
    settings = get_settings()
    if not settings.provider_budget_enabled:
        return None
    if units < 1:
        raise ValueError("positive reservation units required")
    estimate = settings.provider_reservations_usd.get(operation)
    if estimate is None or estimate <= 0 or not math.isfinite(estimate):
        raise ProviderBudgetExceeded("provider operation has no valid cost reservation")
    amount = (Decimal(str(estimate)) * units).quantize(
        Decimal("0.00000001"), rounding=ROUND_CEILING
    )
    ident = str(uuid4())
    try:
        async with asyncio.timeout(5), get_sessionmaker()() as session, session.begin():
            # PostgreSQL chooses the UTC bucket, so process clock skew cannot reset limits.
            day = await session.scalar(text("SELECT (now() AT TIME ZONE 'UTC')::date"))
            admitted = await session.scalar(
                text("""
                INSERT INTO provider_budget_days(day, charged_usd, calls, unknown_calls)
                SELECT CAST(:day AS date), CAST(:amount AS numeric),
                       CAST(:units AS integer), CAST(:units AS integer)
                WHERE CAST(:amount AS numeric) <= CAST(:ceiling AS numeric)
                  AND CAST(:units AS integer) <= CAST(:max_calls AS integer)
                ON CONFLICT (day) DO UPDATE SET
                    charged_usd = provider_budget_days.charged_usd + EXCLUDED.charged_usd,
                    calls = provider_budget_days.calls + EXCLUDED.calls,
                    unknown_calls = provider_budget_days.unknown_calls + EXCLUDED.unknown_calls
                WHERE provider_budget_days.charged_usd + EXCLUDED.charged_usd <= :ceiling
                  AND provider_budget_days.calls + EXCLUDED.calls <= :max_calls
                RETURNING day
                """),
                {
                    "day": day,
                    "amount": amount,
                    "units": units,
                    "ceiling": Decimal(str(settings.provider_daily_budget_usd)),
                    "max_calls": settings.provider_daily_max_calls,
                },
            )
            if admitted is None:
                raise ProviderBudgetExceeded("daily provider allowance exhausted")
            await session.execute(
                text("""
                INSERT INTO provider_reservations(id, day, operation, reserved_usd, units, settled)
                VALUES (:id, :day, :operation, :amount, :units, false)
            """),
                {"id": ident, "day": day, "operation": operation, "amount": amount, "units": units},
            )
    except ProviderBudgetExceeded:
        raise
    except Exception as exc:
        raise ProviderBudgetExceeded("provider allowance ledger unavailable") from exc
    return Reservation(ident)


async def settle(
    reservation: Reservation | None, response: Any = None, *, model: str | None = None
) -> None:
    if reservation is None:
        return
    cost = None
    if response is not None:
        try:
            measured = litellm.completion_cost(completion_response=response, model=model)
            if measured is not None and math.isfinite(measured) and measured >= 0:
                cost = Decimal(str(measured)).quantize(
                    Decimal("0.00000001"), rounding=ROUND_CEILING
                )
        except Exception:
            pass  # unknown is reserved, never assumed free
    try:
        async with asyncio.timeout(5), get_sessionmaker()() as session, session.begin():
            row = (
                await session.execute(
                    text("""
                UPDATE provider_reservations SET settled=true, actual_usd=:cost
                WHERE id=:id AND NOT settled RETURNING day, reserved_usd, units
            """),
                    {"id": reservation.id, "cost": cost},
                )
            ).first()
            if row is not None and cost is not None:
                await session.execute(
                    text("""
                    UPDATE provider_budget_days
                    SET charged_usd=charged_usd+:delta, known_usd=known_usd+:cost,
                        unknown_calls=unknown_calls-:units
                    WHERE day=:day
                """),
                    {
                        "day": row.day,
                        "delta": cost - row.reserved_usd,
                        "cost": cost,
                        "units": row.units,
                    },
                )
    except Exception:
        # Reservation was committed before inference; retaining it is conservative.
        logger.warning("provider allowance settlement unavailable; reservation retained")

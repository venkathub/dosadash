"""Bounded admission stress check in a fresh local *_eval ledger; no provider calls."""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from evals.suites._results import require_eval_database
from sqlalchemy import text

from dosadash_ai.config import get_settings
from dosadash_ai.db import get_sessionmaker
from dosadash_ai.llm.budget import ProviderBudgetExceeded, reserve


async def check() -> dict:
    require_eval_database()
    settings = get_settings()
    if not settings.provider_budget_enabled or settings.provider_daily_budget_usd != 0.1:
        raise ValueError("set enabled=true, daily allowance=.1, default chat estimate=.02")
    if settings.provider_reservations_usd["chat"] != 0.02 or settings.provider_daily_max_calls < 40:
        raise ValueError("requires .02 chat estimate and >=40 call limit")
    async with get_sessionmaker()() as session:
        if await session.scalar(text("SELECT count(*) FROM provider_budget_days")):
            raise ValueError("use a fresh isolated ledger; existing evidence is never deleted")
    slots = asyncio.Semaphore(8)

    async def attempt() -> dict:
        async with slots:
            started = time.monotonic()
            try:
                await reserve("chat")
                status = "admitted"
            except ProviderBudgetExceeded as exc:
                if str(exc) != "daily provider allowance exhausted":
                    raise
                status = "rejected"
            return {"status": status, "latency_ms": (time.monotonic() - started) * 1000}

    started = time.monotonic()
    rows = await asyncio.gather(*(attempt() for _ in range(40)))
    elapsed = time.monotonic() - started
    async with get_sessionmaker()() as session:
        day = (
            await session.execute(
                text("SELECT charged_usd, calls, unknown_calls FROM provider_budget_days")
            )
        ).one()
    assert sum(r["status"] == "admitted" for r in rows) == 5
    assert day.charged_usd == Decimal(".1") and day.calls == 5
    latencies = sorted(r["latency_ms"] for r in rows)
    return {
        "ran_at": datetime.now(UTC).isoformat(),
        "status": "passed",
        "requests": 40,
        "concurrency": 8,
        "admitted": 5,
        "rejected": 35,
        "retained_usd": float(day.charged_usd),
        "elapsed_seconds": round(elapsed, 3),
        "p50_ms": round(latencies[19], 2),
        "p95_ms": round(latencies[37], 2),
        "provider_calls": 0,
        "limitations": (
            "Local PostgreSQL admission stress only; "
            "not production chat throughput or provider latency."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with args.out.open("x") as output:
        json.dump(asyncio.run(check()), output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()

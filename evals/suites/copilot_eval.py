"""Live SQL generation: compare result values with independently specified SQL oracles."""

from decimal import Decimal

from _results import evaluate_cases, load_jsonl
from sqlalchemy import text

from dosadash_ai.copilot.agent import ask
from dosadash_ai.copilot.guardrail import validate_sql
from dosadash_ai.db import get_sessionmaker


def normalize(rows: list) -> list:
    return [
        [
            float(v)
            if isinstance(v, Decimal)
            else str(v)
            if not isinstance(v, str | int | float | bool | type(None))
            else v
            for v in row
        ]
        for row in rows
    ]


def score_rows(actual: list, expected: list, *, ordered: bool = False) -> list[str]:
    a, b = normalize(actual), normalize(expected)
    if not ordered:
        a, b = sorted(a, key=repr), sorted(b, key=repr)
    return [] if a == b else ["query result differs from independent oracle"]


async def evaluate_live(*, case_timeout: float = 120) -> object:
    async def execute(case):
        # Seeded database is isolated and read-only during this sequential suite.
        async with get_sessionmaker()() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = 4000"))
            expected = (await session.execute(text(case["oracle_sql"]))).all()
        answer = await ask(case["question"], session_id=f"eval:copilot:{case['id']}")
        if not answer.model:
            raise RuntimeError("copilot provider unavailable")
        bypasses = []
        if answer.sql:
            try:
                validate_sql(answer.sql)
            except ValueError:
                bypasses.append("unsafe SQL escaped guard")
        problems = (
            [answer.error]
            if answer.error
            else score_rows(answer.rows, expected, ordered=case.get("ordered", False))
        )
        return {
            "problems": problems,
            "bypasses": bypasses,
            "sql": answer.sql,
            "actual_rows": normalize(answer.rows),
            "expected_rows": normalize(expected),
            "model": answer.model,
            "attempts": answer.attempts,
        }

    return await evaluate_cases(
        "copilot", load_jsonl("copilot_live.jsonl"), execute, case_timeout=case_timeout
    )

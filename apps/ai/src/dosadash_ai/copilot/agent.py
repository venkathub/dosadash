"""Copilot flow: question → structured SQL draft → guardrail → read-only
execution → self-correction loop on failure.

Execution guards (beyond the static guardrail): READ ONLY transaction,
statement_timeout, row cap — and optionally a dedicated read-only DB role
when AI_READONLY_DB_PASSWORD is configured (defense in depth).
"""

import logging
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dosadash_ai.config import get_settings
from dosadash_ai.copilot.guardrail import MAX_LIMIT, SqlValidationError, validate_sql
from dosadash_ai.llm.client import LLMError, structured_completion
from dosadash_ai.prompts import load_prompt
from dosadash_shared import COPILOT_PROMPT_VERSION, CellValue, CopilotAnswer, CopilotDraft

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # first draft + two self-corrections
STATEMENT_TIMEOUT_MS = 4000
IST = ZoneInfo("Asia/Kolkata")


@lru_cache
def _engine() -> AsyncEngine:
    settings = get_settings()
    url = settings.copilot_database_url or settings.database_url
    return create_async_engine(url, pool_size=2, max_overflow=2)


def _jsonify(value: object) -> CellValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return float(value)
    return str(value)  # dates, timestamps, enums, UUIDs → ISO-ish strings


async def _run_readonly(sql: str) -> tuple[list[str], list[list[CellValue]]]:
    async with _engine().connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
            result = await conn.execute(text(sql))
            columns = list(result.keys())
            rows = [[_jsonify(v) for v in row] for row in result.fetchmany(MAX_LIMIT)]
    return columns, rows


def _system_prompt() -> str:
    today = datetime.now(IST).date().isoformat()
    return load_prompt(COPILOT_PROMPT_VERSION).replace("{today}", today)


async def ask(
    question: str, *, session_id: str | None = None, user_id: str | None = None
) -> CopilotAnswer:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": question},
    ]
    last_error = "unknown error"
    model_used: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            draft, model_used = await structured_completion(
                messages=messages,
                response_model=CopilotDraft,
                trace_name="copilot_sql",
                prompt_version=COPILOT_PROMPT_VERSION,
                session_id=session_id,
                user_id=user_id,
            )
        except LLMError as exc:
            return CopilotAnswer(question=question, attempts=attempt, error=str(exc))

        try:
            sql = validate_sql(draft.sql)
            columns, rows = await _run_readonly(sql)
        except (SqlValidationError, DBAPIError, SQLAlchemyError) as exc:
            reason = str(getattr(exc, "orig", exc)).splitlines()[0][:300]
            last_error = reason
            logger.info("copilot attempt %d rejected: %s", attempt, reason)
            messages += [
                {"role": "assistant", "content": draft.model_dump_json()},
                {
                    "role": "user",
                    "content": (
                        f"That query failed: {reason}. "
                        "Fix it and return the corrected JSON (same format, all rules apply)."
                    ),
                },
            ]
            continue

        return CopilotAnswer(
            question=question,
            sql=sql,
            explanation=draft.explanation,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=len(rows) >= MAX_LIMIT,
            chart=draft.chart
            if draft.chart.type == "none" or draft.chart.y in columns
            else draft.chart.model_copy(update={"type": "none"}),
            attempts=attempt,
            model=model_used,
        )

    return CopilotAnswer(
        question=question,
        attempts=MAX_ATTEMPTS,
        model=model_used,
        error=f"could not produce a valid query after {MAX_ATTEMPTS} attempts: {last_error}",
    )

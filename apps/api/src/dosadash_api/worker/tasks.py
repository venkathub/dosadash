"""Celery tasks. Sync task bodies wrap async DB work via asyncio.run —
each task creates and disposes its own engine (prefork workers must not
share asyncpg connections across event loops).
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from dosadash_api.config import get_settings
from dosadash_api.worker.celery_app import app

logger = logging.getLogger(__name__)


async def _ping_db() -> bool:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    finally:
        await engine.dispose()


@app.task(name="ops.heartbeat")
def heartbeat() -> dict[str, Any]:
    """Hourly proof-of-life: worker up + DB reachable."""
    db_ok = asyncio.run(_ping_db())
    result = {"at": datetime.now(UTC).isoformat(), "db": db_ok}
    logger.info("heartbeat %s", result)
    return result

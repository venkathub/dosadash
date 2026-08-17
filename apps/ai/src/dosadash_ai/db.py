"""Async DB access for the AI service (same PostgreSQL as the core API).

The AI layer owns only its own tables (rag_chunks); business tables stay
behind the core API. Schema is applied by the api service's alembic
migrations — this module never creates production tables.
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dosadash_ai.config import get_settings


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, pool_size=5, max_overflow=5)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with get_sessionmaker()() as session:
        yield session

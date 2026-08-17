"""AI service test fixtures.

- `rag_session`: real PostgreSQL (pgvector) or clean skip — same convention
  as apps/api/tests (docker on :5433 locally, service container in CI).
- `fake_embedding`: deterministic bag-of-words embedding so vector search
  behaves semantically (shared tokens → higher cosine) without provider keys.
"""

import hashlib
import math
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dosadash_ai.rag.models import RagBase
from dosadash_shared import EMBEDDING_DIM

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://dosadash:dosadash@localhost:5433/dosadash"
)

KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "knowledge"


def fake_embedding(content: str) -> list[float]:
    """Deterministic normalized bag-of-words vector (no provider call)."""
    vec = [0.0] * EMBEDDING_DIM
    for token in re.findall(r"[a-z0-9]+", content.lower()):
        digest = int(hashlib.sha256(token.encode()).hexdigest(), 16)
        vec[digest % EMBEDDING_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


async def fake_embed_texts(texts: list[str], **_: object) -> list[list[float]]:
    return [fake_embedding(t) for t in texts]


async def _try_engine():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=None)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — any connection failure means "no DB here"
        await engine.dispose()
        return None
    return engine


@pytest.fixture
async def rag_session() -> AsyncIterator[AsyncSession]:
    engine = await _try_engine()
    if engine is None:
        pytest.skip(f"no test database at {TEST_DATABASE_URL}")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(RagBase.metadata.drop_all)
        await conn.run_sync(RagBase.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(RagBase.metadata.drop_all)
    await engine.dispose()

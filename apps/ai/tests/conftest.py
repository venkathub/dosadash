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


_AGENT_TABLES = (
    "user_memories",
    "order_items",
    "orders",
    "recipe_ingredients",
    "user_preferences",
    "ingredients",
    "settings",
    "menu_item_translations",
    "menu_items",
)

_AGENT_DDL = (
    """CREATE TABLE menu_items (
        id bigint PRIMARY KEY, name text NOT NULL, category text NOT NULL,
        price numeric(10,2) NOT NULL, is_veg boolean NOT NULL DEFAULT true,
        contains_onion_garlic boolean NOT NULL DEFAULT true,
        spice_level int NOT NULL DEFAULT 1, is_available boolean NOT NULL DEFAULT true,
        schedule jsonb, description text,
        meal_periods jsonb NOT NULL DEFAULT '[]'::jsonb, embedding vector(1536))""",
    """CREATE TABLE settings (
        id int PRIMARY KEY, kitchen_paused boolean NOT NULL DEFAULT false,
        business_hours jsonb)""",
    """CREATE TABLE ingredients (
        id bigint PRIMARY KEY, name text NOT NULL, is_allergen boolean NOT NULL DEFAULT false)""",
    """CREATE TABLE recipe_ingredients (
        item_id bigint NOT NULL, ingredient_id bigint NOT NULL)""",
    """CREATE TABLE user_preferences (
        user_id bigint PRIMARY KEY, diet text, allergens text[] DEFAULT '{}',
        spice_level int, language text DEFAULT 'en')""",
    # Phase 7 localization: minimal shape of the api-owned translations table
    """CREATE TABLE menu_item_translations (
        item_id bigint NOT NULL, lang text NOT NULL, name text NOT NULL,
        status text NOT NULL DEFAULT 'DRAFT', PRIMARY KEY (item_id, lang))""",
    # Phase 6 memory: minimal shapes of the api-owned tables load_memory reads
    """CREATE TABLE orders (
        id bigserial PRIMARY KEY, user_id bigint NOT NULL,
        status text NOT NULL DEFAULT 'PLACED',
        placed_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE order_items (
        order_id bigint NOT NULL, item_id bigint NOT NULL, qty int NOT NULL)""",
    """CREATE TABLE user_memories (
        id bigserial PRIMARY KEY, user_id bigint NOT NULL,
        kind text NOT NULL DEFAULT 'EPISODE', content text NOT NULL,
        at timestamptz NOT NULL DEFAULT now())""",
)

_AGENT_SEED = (
    """INSERT INTO menu_items
        (id, name, category, price, is_veg, contains_onion_garlic, spice_level, is_available,
         meal_periods)
       VALUES
        (1, 'Masala Dosa', 'Dosa', 120.00, true, true, 1, true,
         '["breakfast", "dinner"]'::jsonb),
        (2, 'Cheese Dosa', 'Dosa', 150.00, true, false, 0, true,
         '["breakfast", "dinner"]'::jsonb),
        (3, 'Filter Coffee', 'Beverages', 60.00, true, false, 0, true,
         '["breakfast", "lunch", "snacks", "dinner"]'::jsonb),
        (4, 'Mysore Pak', 'Sweets', 100.00, true, false, 0, false,
         '["snacks"]'::jsonb),
        (5, 'Chicken Biryani', 'Biryani', 220.00, false, true, 2, true,
         '["lunch", "dinner"]'::jsonb)""",
    "INSERT INTO settings (id, kitchen_paused) VALUES (1, false)",
    """INSERT INTO ingredients (id, name, is_allergen) VALUES
        (1, 'mustard seeds', true), (2, 'milk', true), (3, 'potato', false)""",
    """INSERT INTO recipe_ingredients (item_id, ingredient_id) VALUES
        (1, 1), (1, 3), (2, 2), (3, 2)""",
    """INSERT INTO user_preferences (user_id, diet, allergens, spice_level, language)
       VALUES (7, 'veg', '{milk}', 1, 'en')""",
)


@pytest.fixture
async def agent_session(rag_session) -> AsyncSession:
    """rag tables + minimal business tables (columns the agent reads) with a
    small seeded menu. Dropped afterwards so apps/api tests start clean."""
    for table in _AGENT_TABLES:
        await rag_session.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    for ddl in _AGENT_DDL:
        await rag_session.execute(text(ddl))
    for insert in _AGENT_SEED:
        await rag_session.execute(text(insert))
    await rag_session.commit()
    yield rag_session
    for table in _AGENT_TABLES:
        await rag_session.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    await rag_session.commit()

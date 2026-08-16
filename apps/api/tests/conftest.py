"""Integration-test fixtures: real PostgreSQL (pgvector) or clean skip.

Run `docker run -d -p 5433:5432 -e POSTGRES_PASSWORD=dosadash -e
POSTGRES_USER=dosadash pgvector/pgvector:pg16` locally, or rely on the CI
service container. Tests marked `db` skip when no database is reachable.
"""

import os
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dosadash_api.db import Base
from dosadash_api.db.models import Brand, Customization, Ingredient, MenuItem, RecipeIngredient

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://dosadash:dosadash@localhost:5433/dosadash"
)


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
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = await _try_engine()
    if engine is None:
        pytest.skip(f"no test database at {TEST_DATABASE_URL}")
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await _seed_minimal_menu(session)
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _seed_minimal_menu(session: AsyncSession) -> None:
    brand = Brand(name="DosaDash")
    session.add(brand)
    await session.flush()

    peanut = Ingredient(name="peanut", unit="kg", is_allergen=True)
    milk = Ingredient(name="milk", unit="l", is_allergen=True)
    rice = Ingredient(name="idli rice", unit="kg")
    chicken = Ingredient(name="chicken", unit="kg")
    session.add_all([peanut, milk, rice, chicken])
    await session.flush()

    def item(name: str, category: str, price: str, **kw: object) -> MenuItem:
        return MenuItem(
            brand_id=brand.id,
            name=name,
            category=category,
            price=Decimal(price),
            description=f"{name} description",
            **kw,
        )

    masala = item("Masala Dosa", "Dosa", "120", spice_level=1)
    lemon = item("Lemon Rice", "Rice & Pongal", "100", spice_level=1)
    coffee = item("Filter Coffee", "Beverages", "60", spice_level=0)
    biryani = item("Chicken Biryani", "Biryani", "220", is_veg=False, spice_level=2)
    off_menu = item("Seasonal Special", "Dosa", "150", is_available=False)
    session.add_all([masala, lemon, coffee, biryani, off_menu])
    await session.flush()

    session.add_all(
        [
            RecipeIngredient(item_id=masala.id, ingredient_id=rice.id, qty=Decimal("1")),
            RecipeIngredient(item_id=lemon.id, ingredient_id=rice.id, qty=Decimal("1")),
            RecipeIngredient(item_id=lemon.id, ingredient_id=peanut.id, qty=Decimal("1")),
            RecipeIngredient(item_id=coffee.id, ingredient_id=milk.id, qty=Decimal("1")),
            RecipeIngredient(item_id=biryani.id, ingredient_id=chicken.id, qty=Decimal("1")),
            Customization(item_id=masala.id, name="Extra ghee", price_delta=Decimal("20")),
        ]
    )
    await session.commit()


@pytest.fixture
async def client(db_session: AsyncSession):
    import httpx

    from dosadash_api.db.session import get_session
    from dosadash_api.main import app

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

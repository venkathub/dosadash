"""Idempotent DB seeder: menu + synthetic users/orders from dosadash_ml.datagen.

Usage (inside the api container or locally):
    python -m dosadash_api.seed [--days 365] [--users 500] [--seed 42] [--force]

Skips entirely if a brand already exists (unless --force, which only adds
missing users/orders is NOT supported — force drops nothing, it just re-runs
menu-safe inserts; intended for empty databases).
"""

import argparse
import asyncio
from datetime import UTC, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.db.models import (
    Brand,
    Ingredient,
    MenuItem,
    Order,
    OrderItem,
    RecipeIngredient,
    Review,
    Settings,
    User,
    UserPreference,
)
from dosadash_api.db.session import get_sessionmaker
from dosadash_ml.datagen import (
    INGREDIENTS,
    MENU_ITEMS,
    SyntheticOrder,
    SyntheticUser,
    generate_orders,
    generate_reviews,
    generate_users,
    validate_menu,
)
from dosadash_shared import OrderState, Role

GST_RATE = Decimal("0.05")
BATCH = 500


async def _seed_menu(session: AsyncSession) -> tuple[Brand, dict[str, MenuItem]]:
    brand = Brand(name="DosaDash")
    session.add(brand)
    await session.flush()

    ing_rows = {
        i.name: Ingredient(name=i.name, unit=i.unit, is_allergen=i.is_allergen) for i in INGREDIENTS
    }
    session.add_all(ing_rows.values())
    await session.flush()

    item_rows: dict[str, MenuItem] = {}
    for m in MENU_ITEMS:
        item = MenuItem(
            brand_id=brand.id,
            name=m.name,
            description=m.description,
            price=m.price,
            category=m.category,
            is_veg=m.is_veg,
            contains_onion_garlic=m.contains_onion_garlic,
            spice_level=m.spice_level,
            prep_minutes=m.prep_minutes,
            meal_periods=list(m.meal_periods),
        )
        if m.schedule is not None:  # unset → SQL NULL (JSONB stores explicit None as JSON null)
            item.schedule = m.schedule
        item_rows[m.name] = item
    session.add_all(item_rows.values())
    await session.flush()

    session.add_all(
        RecipeIngredient(
            item_id=item_rows[m.name].id,
            ingredient_id=ing_rows[ing_name].id,
            qty=Decimal("1.000"),
        )
        for m in MENU_ITEMS
        for ing_name in m.ingredients
    )
    await session.flush()
    return brand, item_rows


async def _seed_users(session: AsyncSession, synth: list[SyntheticUser]) -> dict[str, User]:
    rows: dict[str, User] = {}
    for su in synth:
        rows[su.phone] = User(phone=su.phone, name=su.name, role=Role.CUSTOMER)
    session.add_all(rows.values())
    await session.flush()
    session.add_all(
        UserPreference(
            user_id=rows[su.phone].id,
            diet=su.persona.diet,
            allergens=list(su.persona.allergens),
            spice_level=su.persona.spice_level,
            language=su.language,
        )
        for su in synth
    )
    await session.flush()
    return rows


async def _seed_orders(
    session: AsyncSession,
    brand: Brand,
    items: dict[str, MenuItem],
    users: dict[str, User],
    synth_orders: list[SyntheticOrder],
) -> list[Order]:
    """Insert orders; the returned list is index-aligned with `synth_orders`
    so review rows can reference their order by generation index."""
    rows: list[Order] = []
    for start in range(0, len(synth_orders), BATCH):
        batch = synth_orders[start : start + BATCH]
        for so in batch:
            subtotal = sum(
                (items[line.item_name].price * line.qty for line in so.items), Decimal("0")
            )
            gst = (subtotal * GST_RATE).quantize(Decimal("0.01"))
            placed_at = so.placed_at.replace(tzinfo=UTC)
            order = Order(
                user_id=users[so.user_phone].id,
                brand_id=brand.id,
                channel=so.channel,
                status=OrderState.DELIVERED,
                subtotal=subtotal,
                gst=gst,
                total=subtotal + gst,
                placed_at=placed_at,
                delivered_at=placed_at + timedelta(minutes=so.delivered_minutes),
            )
            order.items = [
                OrderItem(
                    item_id=items[line.item_name].id,
                    qty=line.qty,
                    unit_price=items[line.item_name].price,
                )
                for line in so.items
            ]
            session.add(order)
            rows.append(order)
        await session.flush()
    return rows


async def _seed_reviews(
    session: AsyncSession,
    users: dict[str, User],
    order_rows: list[Order],
    synth_users: list[SyntheticUser],
    synth_orders: list[SyntheticOrder],
    seed: int,
) -> int:
    """Insert reviews for ~22% of orders. Only rating/text land in the DB —
    the planted aspect labels stay in datagen (training/benchmark ground
    truth must never leak into rows the scoring models can see)."""
    synth_reviews = generate_reviews(synth_users, synth_orders, seed=seed)
    count = 0
    for start in range(0, len(synth_reviews), BATCH):
        for sr in synth_reviews[start : start + BATCH]:
            session.add(
                Review(
                    order_id=order_rows[sr.order_index].id,
                    user_id=users[sr.user_phone].id,
                    rating=sr.rating,
                    text=sr.text,
                    created_at=sr.created_at,  # naive, like all TimestampMixin columns
                )
            )
            count += 1
        await session.flush()
    return count


async def seed(
    *, days: int = 365, users: int = 500, seed_val: int = 42, force: bool = False
) -> None:
    validate_menu()
    async with get_sessionmaker()() as session:
        existing = await session.scalar(select(func.count()).select_from(Brand))
        if existing and not force:
            print(f"seed: skipped ({existing} brand(s) already present)")
            return
        brand, item_rows = await _seed_menu(session)
        synth_users = generate_users(n=users, seed=seed_val)
        synth_orders = generate_orders(synth_users, days=days, seed=seed_val)
        user_rows = await _seed_users(session, synth_users)
        order_rows = await _seed_orders(session, brand, item_rows, user_rows, synth_orders)
        n_reviews = await _seed_reviews(
            session, user_rows, order_rows, synth_users, synth_orders, seed_val
        )
        session.add(Settings(id=1, delivery_pincodes=["600001", "600002", "600004", "600017"]))
        await session.commit()
        print(
            f"seed: ok — {len(item_rows)} menu items, {len(user_rows)} users, "
            f"{len(order_rows)} orders over {days} days, {n_reviews} reviews (seed={seed_val})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed DosaDash DB with menu + synthetic data")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--users", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--make-staff",
        metavar="PHONE",
        help="Promote (or create) this phone as kitchen_staff and exit",
    )
    args = parser.parse_args()
    if args.make_staff:
        asyncio.run(make_staff(args.make_staff))
        return
    asyncio.run(seed(days=args.days, users=args.users, seed_val=args.seed, force=args.force))


async def make_staff(phone: str) -> None:
    from dosadash_api.auth.security import normalize_phone

    normalized = normalize_phone(phone)
    async with get_sessionmaker()() as session:
        user = await session.scalar(select(User).where(User.phone == normalized))
        if user is None:
            user = User(phone=normalized, name="Kitchen Staff", role=Role.KITCHEN_STAFF)
            session.add(user)
        else:
            user.role = Role.KITCHEN_STAFF
        await session.commit()
        print(f"staff: user id={user.id} role={user.role.value}")


if __name__ == "__main__":
    main()

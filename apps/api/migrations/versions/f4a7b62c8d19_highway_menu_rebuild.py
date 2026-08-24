"""Phase 11 highway menu rebuild: serving windows + 99 KM / Manna Mess dishes

The seed catalog is rebuilt around two Chennai–Trichy NH-45 landmarks
(99 KM Coffee veg-millet kitchen, Manna Mess non-veg mess). This migration
brings an EXISTING prod database in line with the new
`dosadash_ml.datagen.MENU_ITEMS` (fresh DBs get everything from the seeder):

1. Backfills `schedule` + `meal_periods` for every seeded dish by name —
   deliberately overwriting prior schedules: the rebuild's whole point is
   tiffin-centre timing ("Dosa is not available in Lunch").
2. Inserts the new ingredients (millets, sukku, karuvadu, crab, prawn) and
   the 12 new dishes with their recipe rows, idempotently by name.
3. 86's the four retired dishes (order history references forbid deletion).

Backfill-by-name pattern per d4a8c31f9e57. New dishes get NULL embeddings —
post-deploy smoke must touch each new item via admin PATCH (or re-run the
re-embed cascade) so RAG/cold-start pick them up.

Revision ID: f4a7b62c8d19
Revises: c8e5a97d3f62
Create Date: 2026-08-21
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4a7b62c8d19"
down_revision: str | None = "c8e5a97d3f62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from dosadash_ml.datagen import INGREDIENTS, MENU_ITEMS
    from dosadash_ml.datagen.menu import RETIRED_DISH_NAMES

    # 1. schedule + meal_periods backfill for every seeded dish (overwrite —
    #    the timing rebuild is the intent, not a gap-fill).
    for m in MENU_ITEMS:
        op.execute(
            sa.text(
                "UPDATE menu_items SET schedule = CAST(:schedule AS jsonb), "
                "meal_periods = CAST(:periods AS jsonb) WHERE name = :name"
            ).bindparams(
                schedule=json.dumps(m.schedule) if m.schedule is not None else None,
                periods=json.dumps(list(m.meal_periods)),
                name=m.name,
            )
        )

    # 2a. new ingredients (idempotent by name; skipped on fresh DBs — see 2b)
    for ing in INGREDIENTS:
        op.execute(
            sa.text(
                "INSERT INTO ingredients (name, unit, is_allergen, stock_qty, reorder_point) "
                "SELECT :name, :unit, :is_allergen, 0, 0 "
                "WHERE EXISTS (SELECT 1 FROM brands) "
                "AND NOT EXISTS (SELECT 1 FROM ingredients WHERE name = :name)"
            ).bindparams(name=ing.name, unit=ing.unit, is_allergen=ing.is_allergen)
        )

    # 2b. new dishes + recipe rows (idempotent by name; brand = the one brand;
    #     skipped entirely on a FRESH/empty DB — no brands row yet means the
    #     seeder owns catalog creation, this migration only upgrades prod data)
    for m in MENU_ITEMS:
        op.execute(
            sa.text(
                "INSERT INTO menu_items (brand_id, name, description, price, category, "
                "is_veg, contains_onion_garlic, spice_level, prep_minutes, meal_periods, "
                "gst_rate, is_available, schedule, image_ai) "
                "SELECT (SELECT id FROM brands ORDER BY id LIMIT 1), :name, :description, "
                ":price, :category, :is_veg, :cog, :spice, :prep, CAST(:periods AS jsonb), "
                "5.00, TRUE, CAST(:schedule AS jsonb), FALSE "
                "WHERE EXISTS (SELECT 1 FROM brands) "
                "AND NOT EXISTS (SELECT 1 FROM menu_items WHERE name = :name)"
            ).bindparams(
                name=m.name,
                description=m.description,
                price=m.price,
                category=m.category,
                is_veg=m.is_veg,
                cog=m.contains_onion_garlic,
                spice=m.spice_level,
                prep=m.prep_minutes,
                periods=json.dumps(list(m.meal_periods)),
                schedule=json.dumps(m.schedule) if m.schedule is not None else None,
            )
        )
        for ing_name in m.ingredients:
            op.execute(
                sa.text(
                    "INSERT INTO recipe_ingredients (item_id, ingredient_id, qty) "
                    "SELECT mi.id, ing.id, 1.000 FROM menu_items mi, ingredients ing "
                    "WHERE mi.name = :item AND ing.name = :ing AND NOT EXISTS ("
                    "SELECT 1 FROM recipe_ingredients r "
                    "WHERE r.item_id = mi.id AND r.ingredient_id = ing.id)"
                ).bindparams(item=m.name, ing=ing_name)
            )

    # 3. retire dropped dishes (86, never delete — order history)
    for name in RETIRED_DISH_NAMES:
        op.execute(
            sa.text("UPDATE menu_items SET is_available = FALSE WHERE name = :name").bindparams(
                name=name
            )
        )


def downgrade() -> None:
    """Best-effort: un-86 retired dishes, remove the new dishes (fails if
    order history already references them — acceptable for a fresh rollback),
    restore the pre-rebuild pongal-only schedules."""
    from dosadash_ml.datagen import MENU_ITEMS
    from dosadash_ml.datagen.menu import RETIRED_DISH_NAMES

    new_names = (
        "Ragi Millet Dosa",
        "Kambu Idli (2 pcs)",
        "Thinai Pongal",
        "Kuzhi Paniyaram (7 pcs)",
        "Mutton Chukka",
        "Nattu Kozhi Kuzhambu",
        "Karuvadu Thokku",
        "Nandu Pepper Fry",
        "Prawn Thokku",
        "Mini Tiffin",
        "Sukku Coffee",
        "Non-Veg Mess Meals",
    )
    for name in new_names:
        op.execute(
            sa.text(
                "DELETE FROM recipe_ingredients WHERE item_id IN "
                "(SELECT id FROM menu_items WHERE name = :name)"
            ).bindparams(name=name)
        )
        op.execute(sa.text("DELETE FROM menu_items WHERE name = :name").bindparams(name=name))
    for name in RETIRED_DISH_NAMES:
        op.execute(
            sa.text("UPDATE menu_items SET is_available = TRUE WHERE name = :name").bindparams(
                name=name
            )
        )
    morning = json.dumps(
        {
            d: {"start": "06:00", "end": "12:00"}
            for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        }
    )
    for m in MENU_ITEMS:
        keep_morning = m.name in ("Ven Pongal", "Sweet Pongal")
        op.execute(
            sa.text(
                "UPDATE menu_items SET schedule = CAST(:schedule AS jsonb) WHERE name = :name"
            ).bindparams(schedule=morning if keep_morning else None, name=m.name)
        )

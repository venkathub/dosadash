from dosadash_api.db import (
    Base,
    models,  # noqa: F401 — imports register the tables
)

EXPECTED_TABLES = {
    "users",
    "otp_requests",
    "refresh_tokens",
    "addresses",
    "user_preferences",
    "brands",
    "menu_items",
    "customizations",
    "combos",
    "ingredients",
    "recipe_ingredients",
    "orders",
    "order_items",
    "payments",
    "coupons",
    "coupon_redemptions",
    "settings",
    "staff_actions",
}


def test_schema_v2_tables_registered():
    assert EXPECTED_TABLES <= set(Base.metadata.tables)


def test_menu_item_has_pgvector_embedding():
    col = Base.metadata.tables["menu_items"].columns["embedding"]
    assert col.type.__class__.__name__ == "VECTOR"
    assert col.type.dim == 1536


def test_recipe_ingredients_composite_pk():
    pk = Base.metadata.tables["recipe_ingredients"].primary_key
    assert {c.name for c in pk.columns} == {"item_id", "ingredient_id"}

"""Synthetic data generator (Phase 0, docs/06).

Deterministic seed → reproducible: 12 months of orders with Pongal/Diwali/Onam
multipliers and weekend biryani spikes, ~500 users with taste personas, and the
~50-item South Indian menu seed with ingredient/allergen/meal-period mapping.

Loaded into the DB by `dosadash_api.seed` (apps/api).
"""

from dosadash_ml.datagen.menu import (
    INGREDIENTS,
    MEAL_PERIODS,
    MENU_ITEMS,
    SeedIngredient,
    SeedMenuItem,
    item_allergens,
    validate_menu,
)
from dosadash_ml.datagen.orders import (
    SyntheticOrder,
    SyntheticOrderItem,
    demand_multiplier,
    generate_orders,
)
from dosadash_ml.datagen.users import PERSONAS, SyntheticUser, generate_users

__all__ = [
    "INGREDIENTS",
    "MEAL_PERIODS",
    "MENU_ITEMS",
    "PERSONAS",
    "SeedIngredient",
    "SeedMenuItem",
    "SyntheticOrder",
    "SyntheticOrderItem",
    "SyntheticUser",
    "demand_multiplier",
    "generate_orders",
    "generate_users",
    "item_allergens",
    "validate_menu",
]

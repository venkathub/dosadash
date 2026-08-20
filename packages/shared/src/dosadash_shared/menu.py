"""Menu API response schemas (shared: web UI, bot rendering, Phase 3 agent).

The order agent's DB-validated item guardrail (Hard Rule 2) validates
against these same shapes — one source of truth for what a menu item is.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CustomizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price_delta: Decimal


class MenuItemSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    description: str | None
    price: Decimal
    is_veg: bool
    spice_level: int
    meal_periods: list[str] = []
    image_url: str | None = None
    allergens: list[str] = []
    # Localization (Phase 7): when ?lang= is served, `name`/`description`
    # carry the APPROVED translation, `canonical_name` keeps the English
    # name (search/debug), and `category_label` is the localized section
    # heading — `category` itself stays canonical as the stable key.
    canonical_name: str | None = None
    category_label: str | None = None


class MenuItemDetail(MenuItemSummary):
    prep_minutes: int
    gst_rate: Decimal
    is_available: bool
    ingredients: list[str] = []
    customizations: list[CustomizationOut] = []
    nutrition: dict | None = None  # owner-APPROVED LLM estimate only (Phase 2)


class CategoryOut(BaseModel):
    name: str
    item_count: int
    label: str | None = None  # localized heading when ?lang= is served

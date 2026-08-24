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
    image_ai: bool = False  # AI-generated photo — always labeled (Phase 7)
    allergens: list[str] = []
    # Localization (Phase 7): when ?lang= is served, `name`/`description`
    # carry the APPROVED translation, `canonical_name` keeps the English
    # name (search/debug), and `category_label` is the localized section
    # heading — `category` itself stays canonical as the stable key.
    canonical_name: str | None = None
    category_label: str | None = None
    # Serving windows (Phase 11 highway rebuild): off-window dishes stay on
    # the menu but are not orderable ("Dosa is not available in Lunch").
    available_now: bool = True
    serving_windows: str | None = None  # human text, e.g. "6–11:30 AM & 5–10 PM"
    # Protein per serving, lifted out of the owner-APPROVED nutrition estimate
    # so the customer menu can filter/sort on it (drafts never surface).
    # None = this dish has no approved estimate yet — never claim a number.
    protein_g: float | None = None


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

"""Admin backoffice schemas (Phase 2) — menu ops payloads.

Structured inputs/outputs everywhere (Hard Rule 3): the admin web UI, tests,
and future agents all share these shapes.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dosadash_shared.menu import MenuItemDetail
from dosadash_shared.schemas import Role

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_PINCODE = re.compile(r"^\d{6}$")


class MenuItemCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    category: str = Field(min_length=2, max_length=60)
    price: Decimal = Field(gt=0, le=Decimal("10000"))
    description: str | None = Field(default=None, max_length=2000)
    is_veg: bool = True
    spice_level: int = Field(default=1, ge=0, le=3)
    prep_minutes: int = Field(default=15, ge=1, le=180)
    gst_rate: Decimal = Field(default=Decimal("5.00"), ge=0, le=28)
    image_url: str | None = Field(default=None, max_length=500)


class MenuItemUpdateIn(BaseModel):
    """Partial update — only fields explicitly set are applied."""

    name: str | None = Field(default=None, min_length=2, max_length=120)
    category: str | None = Field(default=None, min_length=2, max_length=60)
    price: Decimal | None = Field(default=None, gt=0, le=Decimal("10000"))
    description: str | None = Field(default=None, max_length=2000)
    is_veg: bool | None = None
    spice_level: int | None = Field(default=None, ge=0, le=3)
    prep_minutes: int | None = Field(default=None, ge=1, le=180)
    gst_rate: Decimal | None = Field(default=None, ge=0, le=28)
    image_url: str | None = Field(default=None, max_length=500)


class AvailabilityIn(BaseModel):
    """86 toggle body."""

    is_available: bool


class ScheduleWindow(BaseModel):
    """Daily serving window, 24h HH:MM."""

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError("time must be HH:MM (24h)")
        return v


class ScheduleIn(BaseModel):
    """Per-weekday serving windows; null clears the schedule (always on)."""

    schedule: dict[str, ScheduleWindow] | None = None

    @field_validator("schedule")
    @classmethod
    def _known_days(cls, v: dict[str, ScheduleWindow] | None) -> dict[str, ScheduleWindow] | None:
        if v is not None:
            unknown = set(v) - set(WEEKDAYS)
            if unknown:
                raise ValueError(f"unknown day keys: {sorted(unknown)} (use {WEEKDAYS})")
        return v


class CustomizationIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    price_delta: Decimal = Field(default=Decimal("0.00"), ge=-500, le=500)


class MenuItemAdminOut(MenuItemDetail):
    """Admin view: includes 86 state (inherited) plus the schedule JSON."""

    schedule: dict[str, Any] | None = None


# ------------------------------------------------------------------- settings


def _validate_weekdays(v: dict[str, ScheduleWindow] | None) -> dict[str, ScheduleWindow] | None:
    if v is not None:
        unknown = set(v) - set(WEEKDAYS)
        if unknown:
            raise ValueError(f"unknown day keys: {sorted(unknown)} (use {WEEKDAYS})")
    return v


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    business_hours: dict[str, Any] | None = None
    delivery_pincodes: list[str] = []
    kitchen_paused: bool = False


class SettingsUpdateIn(BaseModel):
    """Partial update — only fields explicitly set are applied.

    `business_hours=None` clears the hours (always open).
    """

    business_hours: dict[str, ScheduleWindow] | None = None
    delivery_pincodes: list[str] | None = Field(default=None, max_length=200)

    _days = field_validator("business_hours")(_validate_weekdays)

    @field_validator("delivery_pincodes")
    @classmethod
    def _pincodes(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        bad = [p for p in v if not _PINCODE.match(p)]
        if bad:
            raise ValueError(f"invalid pincodes (need 6 digits): {bad}")
        return sorted(set(v))


class KitchenPauseIn(BaseModel):
    paused: bool
    reason: str | None = Field(default=None, max_length=200)


# ---------------------------------------------------------------- staff RBAC


class RoleUpdateIn(BaseModel):
    role: Role


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone: str
    name: str | None = None
    role: Role
    loyalty_points: int = 0


# -------------------------------------------------------------------- combos


class ComboCreateIn(BaseModel):
    """Combo builder input. Phase 7 AI suggestions land as source=AI_SUGGESTED
    drafts in the same approval flow."""

    name: str = Field(min_length=2, max_length=120)
    item_ids: list[int] = Field(min_length=2, max_length=6)
    price: Decimal = Field(gt=0, le=Decimal("10000"))


class ComboUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    item_ids: list[int] | None = Field(default=None, min_length=2, max_length=6)
    price: Decimal | None = Field(default=None, gt=0, le=Decimal("10000"))


class ComboStatusIn(BaseModel):
    status: Literal["APPROVED", "REJECTED"]


class ComboOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    item_ids: list[int]
    price: Decimal
    source: str
    status: str


# --------------------------------------------------------------- ingredients


class IngredientIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    unit: str = Field(min_length=1, max_length=20)
    is_allergen: bool = False
    supplier: str | None = Field(default=None, max_length=120)
    cost: Decimal | None = Field(default=None, ge=0)
    stock_qty: Decimal = Field(default=Decimal("0"), ge=0)
    reorder_point: Decimal = Field(default=Decimal("0"), ge=0)


class IngredientUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    is_allergen: bool | None = None
    supplier: str | None = Field(default=None, max_length=120)
    cost: Decimal | None = Field(default=None, ge=0)
    stock_qty: Decimal | None = Field(default=None, ge=0)
    reorder_point: Decimal | None = Field(default=None, ge=0)


class IngredientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    unit: str
    is_allergen: bool
    supplier: str | None = None
    cost: Decimal | None = None
    stock_qty: Decimal
    reorder_point: Decimal


# ------------------------------------------------------------- recipe mapping


class RecipeLineIn(BaseModel):
    ingredient_id: int
    qty: Decimal = Field(gt=0)


class RecipeIn(BaseModel):
    """Full-replace recipe mapping. Drives inventory depletion AND the RAG
    allergen knowledge base — single source of truth (docs/06)."""

    lines: list[RecipeLineIn] = Field(min_length=1, max_length=40)

    @field_validator("lines")
    @classmethod
    def _unique_ingredients(cls, v: list[RecipeLineIn]) -> list[RecipeLineIn]:
        ids = [line.ingredient_id for line in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate ingredient_id in recipe lines")
        return v


class RecipeLineOut(BaseModel):
    ingredient_id: int
    name: str
    unit: str
    qty: Decimal
    is_allergen: bool


# ------------------------------------------------------------------ audit log


class StaffActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    action: str
    entity: str
    detail: dict[str, Any] | None = None
    at: datetime

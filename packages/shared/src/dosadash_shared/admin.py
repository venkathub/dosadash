"""Admin backoffice schemas (Phase 2) — menu ops payloads.

Structured inputs/outputs everywhere (Hard Rule 3): the admin web UI, tests,
and future agents all share these shapes.
"""

import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from dosadash_shared.menu import MenuItemDetail

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


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

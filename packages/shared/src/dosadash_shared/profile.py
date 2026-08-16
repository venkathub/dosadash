"""Profile schemas: addresses + dietary preferences.

Shared because the Phase 3 order agent reads preferences ("my usual",
allergen-aware suggestions) and delivery addresses.
"""

from pydantic import BaseModel, ConfigDict, Field

from dosadash_shared.schemas import Diet


class AddressIn(BaseModel):
    label: str = Field(default="Home", max_length=40)
    line1: str = Field(min_length=3, max_length=255)
    pincode: str = Field(pattern=r"^[1-9][0-9]{5}$")
    is_default: bool = False


class AddressPatch(BaseModel):
    label: str | None = Field(default=None, max_length=40)
    line1: str | None = Field(default=None, min_length=3, max_length=255)
    pincode: str | None = Field(default=None, pattern=r"^[1-9][0-9]{5}$")
    is_default: bool | None = None


class AddressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    line1: str
    pincode: str
    is_default: bool


class PreferencesIn(BaseModel):
    diet: Diet | None = None
    allergens: list[str] = Field(default_factory=list, max_length=20)
    spice_level: int | None = Field(default=None, ge=0, le=3)
    language: str = Field(default="en", max_length=8)


class PreferencesOut(PreferencesIn):
    model_config = ConfigDict(from_attributes=True)

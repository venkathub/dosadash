"""Customer profile endpoints: addresses CRUD + dietary preferences.

/api/v1/addresses    — saved delivery addresses (pincode serviceability check)
/api/v1/preferences  — diet / allergens / spice / language (agent reads these)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import CurrentUser
from dosadash_api.db.models import Address, Settings, UserPreference
from dosadash_api.db.session import get_session
from dosadash_shared.profile import (
    AddressIn,
    AddressOut,
    AddressPatch,
    PreferencesIn,
    PreferencesOut,
)

router = APIRouter(prefix="/api/v1", tags=["profile"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _check_serviceable(session: AsyncSession, pincode: str) -> None:
    settings_row = await session.get(Settings, 1)
    allowed = settings_row.delivery_pincodes if settings_row else []
    if allowed and pincode not in allowed:
        raise HTTPException(status_code=422, detail=f"Delivery not available for pincode {pincode}")


async def _unset_other_defaults(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        update(Address).where(Address.user_id == user_id).values(is_default=False)
    )


async def _own_address(session: AsyncSession, user_id: int, address_id: int) -> Address:
    address = await session.get(Address, address_id)
    if address is None or address.user_id != user_id:
        raise HTTPException(status_code=404, detail="Address not found")
    return address


# ---------------------------------------------------------------- addresses


@router.get("/addresses", response_model=list[AddressOut])
async def list_addresses(user: CurrentUser, session: SessionDep) -> list[AddressOut]:
    rows = await session.scalars(
        select(Address).where(Address.user_id == user.id).order_by(Address.id)
    )
    return [AddressOut.model_validate(a) for a in rows]


@router.post("/addresses", response_model=AddressOut, status_code=201)
async def create_address(body: AddressIn, user: CurrentUser, session: SessionDep) -> AddressOut:
    await _check_serviceable(session, body.pincode)
    existing = (await session.scalars(select(Address.id).where(Address.user_id == user.id))).first()
    make_default = body.is_default or existing is None
    if make_default:
        await _unset_other_defaults(session, user.id)
    address = Address(
        user_id=user.id,
        label=body.label,
        line1=body.line1,
        pincode=body.pincode,
        is_default=make_default,
    )
    session.add(address)
    await session.commit()
    return AddressOut.model_validate(address)


@router.patch("/addresses/{address_id}", response_model=AddressOut)
async def update_address(
    address_id: int, body: AddressPatch, user: CurrentUser, session: SessionDep
) -> AddressOut:
    address = await _own_address(session, user.id, address_id)
    if body.pincode is not None:
        await _check_serviceable(session, body.pincode)
        address.pincode = body.pincode
    if body.label is not None:
        address.label = body.label
    if body.line1 is not None:
        address.line1 = body.line1
    if body.is_default:
        await _unset_other_defaults(session, user.id)
        address.is_default = True
    elif body.is_default is False:
        address.is_default = False
    await session.commit()
    return AddressOut.model_validate(address)


@router.delete("/addresses/{address_id}", status_code=204)
async def delete_address(address_id: int, user: CurrentUser, session: SessionDep) -> None:
    address = await _own_address(session, user.id, address_id)
    await session.delete(address)
    await session.commit()


# -------------------------------------------------------------- preferences


@router.get("/preferences", response_model=PreferencesOut)
async def get_preferences(user: CurrentUser, session: SessionDep) -> PreferencesOut:
    prefs = await session.get(UserPreference, user.id)
    if prefs is None:
        return PreferencesOut()
    return PreferencesOut.model_validate(prefs)


@router.put("/preferences", response_model=PreferencesOut)
async def put_preferences(
    body: PreferencesIn, user: CurrentUser, session: SessionDep
) -> PreferencesOut:
    prefs = await session.get(UserPreference, user.id)
    if prefs is None:
        prefs = UserPreference(user_id=user.id)
        session.add(prefs)
    prefs.diet = body.diet
    prefs.allergens = [a.strip().lower() for a in body.allergens if a.strip()]
    prefs.spice_level = body.spice_level
    prefs.language = body.language
    await session.commit()
    return PreferencesOut.model_validate(prefs)

"""Auth endpoints: OTP signup/login, JWT refresh rotation, logout, me.

POST /api/v1/auth/otp/request  — send OTP (DEMO channel returns it for the UI banner)
POST /api/v1/auth/otp/verify   — verify OTP → create user if new → token pair
POST /api/v1/auth/refresh      — rotate refresh token → new pair
POST /api/v1/auth/logout       — revoke refresh token
GET  /api/v1/auth/me           — current user (bearer)

PII (Hard Rule 8): phone numbers are never logged; OTPs stored only as HMAC.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dosadash_api.auth.deps import CurrentUser
from dosadash_api.auth.security import (
    PhoneError,
    create_access_token,
    generate_otp,
    hash_otp,
    hash_refresh_token,
    new_refresh_token,
    normalize_phone,
    verify_otp_hash,
)
from dosadash_api.config import Settings, get_settings
from dosadash_api.db.models import OtpRequest, RefreshToken, User
from dosadash_api.db.session import get_session
from dosadash_api.providers import DemoOtpChannel, OtpChannel
from dosadash_shared import OtpChannelType, Role

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_otp_channel() -> OtpChannel:
    """Phase 1: DEMO channel; Telegram DM channel joins after account linking."""
    return DemoOtpChannel()


OtpChannelDep = Annotated[OtpChannel, Depends(get_otp_channel)]


# ------------------------------------------------------------------- schemas


class OtpRequestIn(BaseModel):
    phone: str


class OtpRequestOut(BaseModel):
    channel: OtpChannelType
    expires_in: int
    resend_after: int
    demo_otp: str | None = None  # DEMO channel only — rendered in the UI banner


class OtpVerifyIn(BaseModel):
    phone: str
    otp: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone: str
    name: str | None
    role: Role
    loyalty_points: int


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class RefreshIn(BaseModel):
    refresh_token: str


# ------------------------------------------------------------------- helpers


def _normalize_or_400(raw: str) -> str:
    try:
        return normalize_phone(raw)
    except PhoneError as exc:
        raise HTTPException(status_code=400, detail="Invalid phone number") from exc


async def _issue_tokens(session: AsyncSession, user: User, settings: Settings) -> TokenPair:
    access = create_access_token(
        user_id=user.id,
        role=user.role,
        secret=settings.jwt_secret,
        ttl_minutes=settings.access_ttl_minutes,
    )
    refresh, refresh_hash = new_refresh_token()
    session.add(RefreshToken(user_id=user.id, token_hash=refresh_hash))
    await session.commit()
    return TokenPair(access_token=access, refresh_token=refresh, user=UserOut.model_validate(user))


# ----------------------------------------------------------------- endpoints


@router.post("/otp/request", response_model=OtpRequestOut)
async def request_otp(
    body: OtpRequestIn,
    session: SessionDep,
    settings: SettingsDep,
    channel: OtpChannelDep,
) -> OtpRequestOut:
    phone = _normalize_or_400(body.phone)
    now = datetime.now(UTC)

    latest = await session.scalar(
        select(OtpRequest)
        .where(OtpRequest.phone == phone)
        .order_by(OtpRequest.created_at.desc())
        .limit(1)
    )
    if latest is not None:
        elapsed = (now - latest.created_at.replace(tzinfo=UTC)).total_seconds()
        if elapsed < settings.otp_resend_cooldown_seconds:
            raise HTTPException(status_code=429, detail="OTP recently sent — wait before retrying")

    otp = generate_otp()
    session.add(
        OtpRequest(
            phone=phone,
            otp_hash=hash_otp(phone, otp, settings.jwt_secret),
            channel=channel.channel_type,
            expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
        )
    )
    await session.commit()

    result = await channel.send_otp(phone, otp)
    if not result.delivered:
        raise HTTPException(status_code=502, detail="OTP delivery failed")
    return OtpRequestOut(
        channel=result.channel,
        expires_in=settings.otp_ttl_seconds,
        resend_after=settings.otp_resend_cooldown_seconds,
        demo_otp=result.demo_otp,
    )


@router.post("/otp/verify", response_model=TokenPair)
async def verify_otp(body: OtpVerifyIn, session: SessionDep, settings: SettingsDep) -> TokenPair:
    phone = _normalize_or_400(body.phone)
    now = datetime.now(UTC)

    otp_row = await session.scalar(
        select(OtpRequest)
        .where(OtpRequest.phone == phone)
        .order_by(OtpRequest.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if otp_row is None or otp_row.expires_at.replace(tzinfo=UTC) < now:
        raise HTTPException(status_code=400, detail="OTP expired — request a new one")
    if otp_row.attempts >= settings.otp_max_attempts:
        raise HTTPException(status_code=429, detail="Too many attempts — request a new OTP")

    otp_row.attempts += 1
    if not verify_otp_hash(phone, body.otp, settings.jwt_secret, otp_row.otp_hash):
        await session.commit()  # persist the attempt count
        raise HTTPException(status_code=400, detail="Incorrect OTP")

    otp_row.expires_at = now  # single-use: burn it
    user = await session.scalar(select(User).where(User.phone == phone))
    if user is None:
        user = User(phone=phone, role=Role.CUSTOMER)
        session.add(user)
        await session.flush()
    return await _issue_tokens(session, user, settings)


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(body: RefreshIn, session: SessionDep, settings: SettingsDep) -> TokenPair:
    token_hash = hash_refresh_token(body.refresh_token)
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )
    now = datetime.now(UTC)
    max_age = timedelta(days=settings.refresh_ttl_days)
    if row is None or row.revoked or (now - row.created_at.replace(tzinfo=UTC)) > max_age:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    row.revoked = True
    row.rotated_at = now
    user = await session.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return await _issue_tokens(session, user, settings)


@router.post("/logout", status_code=204)
async def logout(body: RefreshIn, session: SessionDep) -> None:
    token_hash = hash_refresh_token(body.refresh_token)
    row = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if row is not None:
        row.revoked = True
        await session.commit()


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)

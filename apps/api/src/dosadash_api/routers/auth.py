"""Auth endpoints: OTP signup/login, JWT refresh rotation, logout, me.

POST /api/v1/auth/otp/request  — send OTP (DEMO channel returns it for the UI banner)
POST /api/v1/auth/otp/verify   — verify OTP → create user if new → token pair
POST /api/v1/auth/refresh      — rotate refresh token → new pair
POST /api/v1/auth/logout       — revoke refresh token
GET  /api/v1/auth/me           — current user (bearer)

PII (Hard Rule 8): phone numbers are never logged; OTPs stored only as HMAC.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
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
from dosadash_api.events import get_redis
from dosadash_api.providers import DemoOtpChannel, OtpChannel, TelegramOtpChannel
from dosadash_shared import OtpChannelType, Role

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_otp_channel() -> OtpChannel:
    """Default channel (kept as a dependency for test overrides)."""
    return DemoOtpChannel()


OtpChannelDep = Annotated[OtpChannel, Depends(get_otp_channel)]


async def _select_channel(
    session: AsyncSession, phone: str, settings: Settings, default: OtpChannel
) -> OtpChannel:
    """Telegram DM when the phone belongs to a linked account, else default."""
    if not settings.telegram_bot_token:
        return default
    user = await session.scalar(select(User).where(User.phone == phone))
    if user is None or user.tg_user_id is None:
        return default
    return TelegramOtpChannel(settings.telegram_bot_token, user.tg_user_id)


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
    tg_linked: bool = False


def _user_out(user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.tg_linked = user.tg_user_id is not None
    return out


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
    return TokenPair(access_token=access, refresh_token=refresh, user=_user_out(user))


# ----------------------------------------------------------------- endpoints


@router.post("/otp/request", response_model=OtpRequestOut)
async def request_otp(
    body: OtpRequestIn,
    session: SessionDep,
    settings: SettingsDep,
    default_channel: OtpChannelDep,
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

    channel = await _select_channel(session, phone, settings, default_channel)
    otp = generate_otp()
    result = await channel.send_otp(phone, otp)
    if not result.delivered and channel.channel_type != OtpChannelType.DEMO:
        # Telegram DM failed → fall back to the demo banner
        channel = default_channel
        result = await channel.send_otp(phone, otp)
    if not result.delivered:
        raise HTTPException(status_code=502, detail="OTP delivery failed")

    session.add(
        OtpRequest(
            phone=phone,
            otp_hash=hash_otp(phone, otp, settings.jwt_secret),
            channel=result.channel,
            expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
        )
    )
    await session.commit()
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
    return _user_out(user)


# ------------------------------------------------------- telegram linking


LINK_CODE_TTL_SECONDS = 600


class LinkCodeOut(BaseModel):
    code: str
    deep_link: str
    expires_in: int = LINK_CODE_TTL_SECONDS


class TelegramLinkIn(BaseModel):
    code: str
    tg_user_id: int
    tg_name: str | None = None


class TelegramLinkOut(BaseModel):
    linked: bool
    name: str | None


@router.post("/telegram/link-code", response_model=LinkCodeOut)
async def telegram_link_code(user: CurrentUser, settings: SettingsDep) -> LinkCodeOut:
    """Generate a short-lived deep-link code for t.me/<bot>?start=<code>."""
    code = secrets.token_urlsafe(12)
    await get_redis().setex(f"tg_link:{code}", LINK_CODE_TTL_SECONDS, user.id)
    return LinkCodeOut(
        code=code,
        deep_link=f"https://t.me/{settings.telegram_bot_username}?start={code}",
    )


@router.post("/telegram/link", response_model=TelegramLinkOut)
async def telegram_link(
    body: TelegramLinkIn,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> TelegramLinkOut:
    """Internal endpoint (bot → api): consume a link code, attach tg_user_id."""
    if not settings.internal_api_token:
        raise HTTPException(status_code=503, detail="Linking not configured")
    provided = request.headers.get("X-Internal-Token", "")
    if not secrets.compare_digest(provided, settings.internal_api_token):
        raise HTTPException(status_code=403, detail="Forbidden")

    key = f"tg_link:{body.code}"
    redis = get_redis()
    user_id = await redis.get(key)
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired link code")
    await redis.delete(key)  # single-use

    existing = await session.scalar(select(User).where(User.tg_user_id == body.tg_user_id))
    if existing is not None and existing.id != int(user_id):
        raise HTTPException(
            status_code=409, detail="This Telegram account is linked to another user"
        )
    user = await session.get(User, int(user_id))
    if user is None:
        raise HTTPException(status_code=400, detail="User not found")
    user.tg_user_id = body.tg_user_id
    if body.tg_name and not user.name:
        user.name = body.tg_name
    await session.commit()
    return TelegramLinkOut(linked=True, name=user.name)


@router.delete("/telegram/link", status_code=204)
async def telegram_unlink(user: CurrentUser, session: SessionDep) -> None:
    """Unlink the current user's Telegram account (OTPs revert to demo banner)."""
    user.tg_user_id = None
    await session.commit()

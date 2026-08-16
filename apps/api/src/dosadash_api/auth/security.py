"""Pure crypto/token helpers (no I/O — unit-testable)."""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from dosadash_shared import Role

ALGORITHM = "HS256"
_PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")


class PhoneError(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """Normalize to E.164-ish (+91 default for bare 10-digit numbers)."""
    cleaned = re.sub(r"[\s\-()]", "", raw)
    if not _PHONE_RE.match(cleaned):
        raise PhoneError("invalid phone number")
    digits = cleaned.lstrip("+")
    if len(digits) == 10:
        return f"+91{digits}"
    return f"+{digits}"


def generate_otp() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_otp(phone: str, otp: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"{phone}:{otp}".encode(), hashlib.sha256).hexdigest()


def verify_otp_hash(phone: str, otp: str, secret: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(phone, otp, secret), expected_hash)


def create_access_token(*, user_id: int, role: Role, secret: str, ttl_minutes: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role.value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, secret: str) -> dict[str, str]:
    """Decode + verify; raises jwt.PyJWTError on any problem."""
    return jwt.decode(token, secret, algorithms=[ALGORITHM])


def new_refresh_token() -> tuple[str, str]:
    """Returns (opaque token for client, sha256 hash for storage)."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

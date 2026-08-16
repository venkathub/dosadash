import time

import jwt as pyjwt
import pytest

from dosadash_api.auth.security import (
    PhoneError,
    create_access_token,
    decode_access_token,
    generate_otp,
    hash_otp,
    hash_refresh_token,
    new_refresh_token,
    normalize_phone,
    verify_otp_hash,
)
from dosadash_shared import Role


def test_normalize_phone_variants():
    assert normalize_phone("9876543210") == "+919876543210"
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    assert normalize_phone("+1-415-555-0100") == "+14155550100"
    with pytest.raises(PhoneError):
        normalize_phone("dosa")
    with pytest.raises(PhoneError):
        normalize_phone("12345")


def test_otp_hash_roundtrip():
    otp = generate_otp()
    assert len(otp) == 6 and otp.isdigit()
    h = hash_otp("+919876543210", otp, "secret")
    assert verify_otp_hash("+919876543210", otp, "secret", h)
    assert not verify_otp_hash("+919876543210", "000000", "secret", h)
    assert not verify_otp_hash("+919876543211", otp, "secret", h)


def test_access_token_roundtrip():
    token = create_access_token(user_id=7, role=Role.ADMIN, secret="s", ttl_minutes=5)
    payload = decode_access_token(token, "s")
    assert payload["sub"] == "7"
    assert payload["role"] == "admin"
    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token(token, "wrong-secret")


def test_access_token_expiry():
    token = create_access_token(user_id=1, role=Role.CUSTOMER, secret="s", ttl_minutes=0)
    time.sleep(1.1)
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_access_token(token, "s")


def test_refresh_token_hash():
    token, token_hash = new_refresh_token()
    assert hash_refresh_token(token) == token_hash
    assert token not in token_hash

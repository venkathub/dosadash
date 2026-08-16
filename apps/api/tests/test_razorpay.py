"""Razorpay provider + webhook tests (no real API calls — mock transport)."""

import hashlib
import hmac
import json
from decimal import Decimal

import httpx
import pytest

from dosadash_api.config import get_settings
from dosadash_api.providers import (
    MockPaymentProvider,
    RazorpayProvider,
    select_payment_provider,
    verify_webhook_signature,
)

KEY_ID = "rzp_test_dummy"
KEY_SECRET = "test_secret"


def test_signature_verify_matches_razorpay_scheme():
    provider = RazorpayProvider(KEY_ID, KEY_SECRET)
    sig = hmac.new(KEY_SECRET.encode(), b"order_abc|pay_xyz", hashlib.sha256).hexdigest()
    assert provider.verify_signature(order_id="order_abc", payment_id="pay_xyz", signature=sig)
    assert not provider.verify_signature(
        order_id="order_abc", payment_id="pay_xyz", signature="tampered"
    )


async def test_create_order_sends_paise_and_auth():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "order_LIVEID123", "status": "created"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = RazorpayProvider(KEY_ID, KEY_SECRET, client=client)
    order = await provider.create_order(amount=Decimal("126.00"))

    assert captured["url"] == "https://api.razorpay.com/v1/orders"
    assert captured["auth"].startswith("Basic ")
    assert captured["body"] == {"amount": 12600, "currency": "INR"}  # paise
    assert order.provider_order_id == "order_LIVEID123"
    assert order.provider == "razorpay"
    await client.aclose()


def test_provider_selection():
    assert isinstance(
        select_payment_provider(provider="auto", razorpay_key_id="", razorpay_key_secret=""),
        MockPaymentProvider,
    )
    assert isinstance(
        select_payment_provider(
            provider="auto", razorpay_key_id=KEY_ID, razorpay_key_secret=KEY_SECRET
        ),
        RazorpayProvider,
    )
    assert isinstance(
        select_payment_provider(
            provider="mock", razorpay_key_id=KEY_ID, razorpay_key_secret=KEY_SECRET
        ),
        MockPaymentProvider,
    )


def test_webhook_signature_helper():
    body = b'{"event":"payment.captured"}'
    sig = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body=body, signature=sig, webhook_secret="whsec")
    assert not verify_webhook_signature(body=body, signature=sig, webhook_secret="other")


# ------------------------------------------------------------------ webhook API


@pytest.fixture
def webhook_secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "razorpay_webhook_secret", "whsec_test")
    return "whsec_test"


def _signed(body: dict, secret: str) -> tuple[bytes, str]:
    raw = json.dumps(body).encode()
    return raw, hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


async def test_webhook_rejects_bad_signature(client, webhook_secret):
    raw, _ = _signed({"event": "payment.captured"}, "wrong-secret")
    resp = await client.post(
        "/api/v1/payments/razorpay/webhook",
        content=raw,
        headers={"X-Razorpay-Signature": "garbage", "Content-Type": "application/json"},
    )
    assert resp.status_code == 400


async def test_webhook_captures_payment(client, db_session, webhook_secret):
    # place an order through the API (mock provider in tests)
    req = await client.post("/api/v1/auth/otp/request", json={"phone": "9666666661"})
    otp = req.json()["demo_otp"]
    tokens = (
        await client.post("/api/v1/auth/otp/verify", json={"phone": "9666666661", "otp": otp})
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    menu = (await client.get("/api/v1/menu")).json()
    order = (
        await client.post(
            "/api/v1/orders",
            headers=headers,
            json={"items": [{"item_id": menu[0]["id"], "qty": 1}]},
        )
    ).json()
    provider_order_id = order["payment"]["provider_order_id"]

    raw, sig = _signed(
        {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": "pay_wh_1", "order_id": provider_order_id}}},
        },
        webhook_secret,
    )
    resp = await client.post(
        "/api/v1/payments/razorpay/webhook",
        content=raw,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "handled": True}

    detail = (await client.get(f"/api/v1/orders/{order['id']}", headers=headers)).json()
    assert detail["payment"]["status"] == "CAPTURED"
    assert detail["payment"]["signature_verified"] is True


async def test_webhook_unknown_order_acknowledged_not_handled(client, webhook_secret):
    raw, sig = _signed(
        {
            "event": "payment.failed",
            "payload": {"payment": {"entity": {"id": "pay_x", "order_id": "order_nonexistent"}}},
        },
        webhook_secret,
    )
    resp = await client.post(
        "/api/v1/payments/razorpay/webhook",
        content=raw,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["handled"] is False


async def test_payment_config_endpoint(client):
    resp = await client.get("/api/v1/payments/config")
    assert resp.status_code == 200
    assert resp.json() == {"provider": "mock", "key_id": None}  # no keys in test env

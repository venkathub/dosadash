"""Payment provider interface + Phase 0 mock implementation.

The mock mirrors Razorpay's shape (order id, HMAC-SHA256 signature over
`order_id|payment_id`) so the Phase 1 Razorpay TEST-key implementation is a
drop-in swap.
"""

import hashlib
import hmac
import secrets
from abc import ABC, abstractmethod
from decimal import Decimal

import httpx
from pydantic import BaseModel

from dosadash_shared import PaymentStatus


class ProviderOrder(BaseModel):
    provider: str
    provider_order_id: str
    amount: Decimal
    currency: str = "INR"
    status: PaymentStatus = PaymentStatus.CREATED


class RefundResult(BaseModel):
    provider: str
    refund_id: str
    status: PaymentStatus = PaymentStatus.REFUNDED


class PaymentProvider(ABC):
    """Interface for payment gateways (Razorpay TEST in prod, mock in dev/CI)."""

    name: str

    @abstractmethod
    async def create_order(self, *, amount: Decimal, currency: str = "INR") -> ProviderOrder:
        """Create a provider-side order for checkout."""

    @abstractmethod
    def verify_signature(self, *, order_id: str, payment_id: str, signature: str) -> bool:
        """Verify a payment/webhook signature. Sync + pure — no I/O."""

    @abstractmethod
    async def refund(self, *, payment_id: str, amount: Decimal) -> RefundResult:
        """Refund a captured payment (full or partial)."""


class RazorpayProvider(PaymentProvider):
    """Razorpay REST integration — TEST keys only (Hard Rule 9).

    Signature scheme is HMAC-SHA256 over "order_id|payment_id" with the key
    secret — exactly what MockPaymentProvider mimics, so both satisfy the
    same interface and tests.
    """

    name = "razorpay"
    BASE_URL = "https://api.razorpay.com/v1"

    def __init__(
        self, key_id: str, key_secret: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self.key_id = key_id
        self._auth = (key_id, key_secret)
        self._secret = key_secret.encode()
        self._client = client  # injectable for tests

    async def _request(self, method: str, path: str, json_body: dict) -> dict:
        if self._client is not None:
            resp = await self._client.request(
                method, f"{self.BASE_URL}{path}", auth=self._auth, json=json_body
            )
        else:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.request(
                    method, f"{self.BASE_URL}{path}", auth=self._auth, json=json_body
                )
        resp.raise_for_status()
        return resp.json()

    async def create_order(self, *, amount: Decimal, currency: str = "INR") -> ProviderOrder:
        data = await self._request(
            "POST",
            "/orders",
            {"amount": int(amount * 100), "currency": currency},  # paise
        )
        return ProviderOrder(
            provider=self.name,
            provider_order_id=data["id"],
            amount=amount,
            currency=currency,
        )

    def verify_signature(self, *, order_id: str, payment_id: str, signature: str) -> bool:
        payload = f"{order_id}|{payment_id}".encode()
        expected = hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def refund(self, *, payment_id: str, amount: Decimal) -> RefundResult:
        data = await self._request(
            "POST", f"/payments/{payment_id}/refund", {"amount": int(amount * 100)}
        )
        return RefundResult(provider=self.name, refund_id=data["id"])


def verify_webhook_signature(*, body: bytes, signature: str, webhook_secret: str) -> bool:
    """Razorpay webhook: HMAC-SHA256 of the raw body with the webhook secret."""
    expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def select_payment_provider(
    *, provider: str, razorpay_key_id: str, razorpay_key_secret: str
) -> PaymentProvider:
    """Provider selection (Hard Rule 1): explicit setting or auto-detect."""
    use_razorpay = provider == "razorpay" or (
        provider == "auto" and razorpay_key_id and razorpay_key_secret
    )
    if use_razorpay:
        return RazorpayProvider(razorpay_key_id, razorpay_key_secret)
    return MockPaymentProvider()


class MockPaymentProvider(PaymentProvider):
    """Deterministic in-memory provider for dev, tests, and CI."""

    name = "mock"

    def __init__(self, secret: str = "mock-secret") -> None:
        self._secret = secret.encode()

    async def create_order(self, *, amount: Decimal, currency: str = "INR") -> ProviderOrder:
        return ProviderOrder(
            provider=self.name,
            provider_order_id=f"order_mock_{secrets.token_hex(8)}",
            amount=amount,
            currency=currency,
        )

    def sign(self, *, order_id: str, payment_id: str) -> str:
        """Produce a signature (test helper — real gateways sign server-side)."""
        payload = f"{order_id}|{payment_id}".encode()
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def verify_signature(self, *, order_id: str, payment_id: str, signature: str) -> bool:
        expected = self.sign(order_id=order_id, payment_id=payment_id)
        return hmac.compare_digest(expected, signature)

    async def refund(self, *, payment_id: str, amount: Decimal) -> RefundResult:
        return RefundResult(provider=self.name, refund_id=f"rfnd_mock_{secrets.token_hex(8)}")


class AggregatorPrepaidProvider(MockPaymentProvider):
    """Aggregator orders arrive prepaid — the aggregator collected payment
    and settles offline (Phase 7 mock channel). Same PaymentProvider
    interface (Hard Rule 1): the payment row is created through it and
    immediately marked CAPTURED by the ingest service; refunds resolve
    mock-side like the dev provider."""

    name = "aggregator"


# Phase 1: RazorpayProvider(PaymentProvider) — TEST keys only (Hard Rule 9).

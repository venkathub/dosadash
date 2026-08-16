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


# Phase 1: RazorpayProvider(PaymentProvider) — TEST keys only (Hard Rule 9).

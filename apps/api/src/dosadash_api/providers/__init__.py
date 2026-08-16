"""Provider interfaces (Hard Rule 1): payments and OTP delivery are swappable.

Services depend on the ABCs; concrete implementations are selected via
settings. Phase 0 ships working stubs (mock payments, demo OTP); Razorpay and
Telegram implementations arrive in Phase 1.
"""

from dosadash_api.providers.otp import DemoOtpChannel, OtpChannel, OtpSendResult
from dosadash_api.providers.payments import (
    MockPaymentProvider,
    PaymentProvider,
    ProviderOrder,
    RefundResult,
)

__all__ = [
    "DemoOtpChannel",
    "MockPaymentProvider",
    "OtpChannel",
    "OtpSendResult",
    "PaymentProvider",
    "ProviderOrder",
    "RefundResult",
]

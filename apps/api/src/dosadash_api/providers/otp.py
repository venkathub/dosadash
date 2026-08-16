"""OTP delivery channel interface + Phase 0 demo implementation.

PII note (Hard Rule 8): implementations must never log the full phone number;
use `mask_phone` for any diagnostics.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from dosadash_shared import OtpChannelType


def mask_phone(phone: str) -> str:
    """Redact a phone number for logs: keep last 3 digits only."""
    digits = "".join(c for c in phone if c.isdigit())
    return f"***{digits[-3:]}" if len(digits) >= 3 else "***"


class OtpSendResult(BaseModel):
    delivered: bool
    channel: OtpChannelType
    # DEMO channel only: surfaced in the UI banner instead of being sent.
    demo_otp: str | None = None


class OtpChannel(ABC):
    """Interface for delivering an OTP to a phone number."""

    channel_type: OtpChannelType

    @abstractmethod
    async def send_otp(self, phone: str, otp: str) -> OtpSendResult:
        """Deliver `otp` to `phone`. Must not raise on delivery failure —
        return `delivered=False` so the caller can fall back."""


class DemoOtpChannel(OtpChannel):
    """Demo-mode delivery: the OTP is returned to the caller and shown in a
    UI banner (portfolio deployment has no SMS gateway)."""

    channel_type = OtpChannelType.DEMO

    async def send_otp(self, phone: str, otp: str) -> OtpSendResult:
        return OtpSendResult(delivered=True, channel=self.channel_type, demo_otp=otp)


# Phase 1: TelegramOtpChannel — DMs the OTP via the linked Telegram account.

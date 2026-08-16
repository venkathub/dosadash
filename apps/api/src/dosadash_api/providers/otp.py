"""OTP delivery channel interface + implementations.

PII note (Hard Rule 8): implementations must never log the full phone number;
use `mask_phone` for any diagnostics.
"""

import logging
from abc import ABC, abstractmethod

import httpx
from pydantic import BaseModel

from dosadash_shared import OtpChannelType

logger = logging.getLogger(__name__)


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


class TelegramOtpChannel(OtpChannel):
    """DMs the OTP via the linked Telegram account (users.tg_user_id)."""

    channel_type = OtpChannelType.TELEGRAM

    def __init__(
        self, bot_token: str, tg_user_id: int, client: httpx.AsyncClient | None = None
    ) -> None:
        self._bot_token = bot_token
        self._tg_user_id = tg_user_id
        self._client = client  # injectable for tests

    async def send_otp(self, phone: str, otp: str) -> OtpSendResult:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._tg_user_id,
            "text": f"🥞 Your DosaDash login OTP is: {otp}\nValid for 5 minutes.",
        }
        try:
            if self._client is not None:
                resp = await self._client.post(url, json=payload)
            else:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(url, json=payload)
            delivered = resp.status_code == 200 and resp.json().get("ok", False)
        except httpx.HTTPError:
            delivered = False
        if not delivered:
            logger.warning("telegram OTP delivery failed for %s", mask_phone(phone))
        return OtpSendResult(delivered=delivered, channel=self.channel_type)

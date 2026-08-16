"""Core cross-service schemas and enums.

These are the canonical definitions — services must import from here rather
than redefining order states or roles locally.
"""

from enum import StrEnum

from pydantic import BaseModel


class OrderState(StrEnum):
    """Order lifecycle states (transitions only via order_service state machine)."""

    PLACED = "PLACED"
    CONFIRMED = "CONFIRMED"
    COOKING = "COOKING"
    READY = "READY"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class Role(StrEnum):
    """RBAC roles carried in JWT claims."""

    CUSTOMER = "customer"
    KITCHEN_STAFF = "kitchen_staff"
    ADMIN = "admin"
    OWNER = "owner"


class Diet(StrEnum):
    """Dietary preference (docs/06 user_preferences)."""

    VEG = "VEG"
    VEGAN = "VEGAN"
    JAIN = "JAIN"
    NONVEG = "NONVEG"


class ChannelType(StrEnum):
    """Order/chat channel (docs/06 channels)."""

    WEB = "WEB"
    TELEGRAM = "TELEGRAM"
    MOCK_AGGREGATOR = "MOCK_AGGREGATOR"


class OtpChannelType(StrEnum):
    """OTP delivery channel (docs/06 otp_requests)."""

    DEMO = "DEMO"
    TELEGRAM = "TELEGRAM"


class CouponType(StrEnum):
    """Coupon discount type (docs/06 coupons)."""

    PCT = "PCT"
    FLAT = "FLAT"


class PaymentStatus(StrEnum):
    """Payment lifecycle at the provider."""

    CREATED = "CREATED"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class HealthStatus(BaseModel):
    """Response body for /healthz endpoints across all services."""

    status: str = "ok"
    service: str
    version: str = "0.1.0"

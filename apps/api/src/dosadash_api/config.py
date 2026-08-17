"""Service settings loaded from environment variables (never hardcode secrets)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Core API settings. All values come from env vars / .env."""

    model_config = SettingsConfigDict(env_prefix="API_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://dosadash:dosadash@localhost:5432/dosadash"
    redis_url: str = "redis://localhost:6379/0"

    # Auth (Phase 1) — override jwt_secret in every real deployment
    jwt_secret: str = "dev-secret-do-not-use-in-prod"
    access_ttl_minutes: int = 30
    refresh_ttl_days: int = 30
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 45

    # Payments (Hard Rule 9: Razorpay TEST keys only)
    # "auto" → razorpay when TEST keys are present, else mock
    payment_provider: str = "auto"  # auto | mock | razorpay
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # Telegram linking + DM OTP channel
    telegram_bot_token: str = ""
    telegram_bot_username: str = "dosadash_bot"
    # Shared secret for bot→api internal calls (both services get the same value)
    internal_api_token: str = ""

    # AI service base URL (docker network name in prod, localhost in dev).
    ai_base_url: str = "http://ai:8001"


@lru_cache
def get_settings() -> Settings:
    return Settings()

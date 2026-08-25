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

    # Rate limiting (Phase 9 hardening) — fixed 60s windows, per-tier caps.
    # Fail-open on Redis outage; internal-token traffic exempt (see ratelimit.py).
    rate_limit_enabled: bool = True
    rate_limit_chat_per_minute: int = 20
    rate_limit_auth_per_minute: int = 10
    rate_limit_write_per_minute: int = 60
    rate_limit_read_per_minute: int = 240
    # Feedback intake (Phase 13) — strictest write tier: reports are rare,
    # spam floods GitHub issues + LLM triage spend. Identity is user-or-IP,
    # so this also caps anonymous reporters per IP.
    rate_limit_feedback_per_minute: int = 5

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

    # Mock-aggregator channel (Phase 7, docs/04 O12): HMAC secret for the
    # simulated partner webhook (Razorpay-webhook pattern). Empty → 503.
    aggregator_webhook_secret: str = ""

    # Media storage (Phase 7 image gen): AI dish photos live here, served
    # at /media. In compose this is a named volume so images survive deploys.
    media_dir: str = "media"

    # AI service base URL (docker network name in prod, localhost in dev).
    ai_base_url: str = "http://ai:8001"
    # Bot base URL for api→bot internal calls (Phase 6 owner PO notifications).
    # Empty string disables notifications (backoffice tab still shows drafts).
    bot_base_url: str = "http://bot:8081"

    # Self-healing loop (Phase 13, docs/14): user feedback → GitHub issues.
    # Token = fine-grained PAT or GitHub App installation token scoped to ONE
    # repo (issues:write). Either empty → reports are stored locally only
    # (graceful degrade — GitHub is never on the customer's critical path).
    github_token: str = ""
    github_repo: str = ""  # "owner/repo"
    # Phase 14 lifecycle sync: HMAC secret for the GitHub → api webhook
    # (X-Hub-Signature-256). Empty → webhook 503s; the beat reconciler
    # still keeps the loop's tail in sync at 15-min freshness.
    github_webhook_secret: str = ""

    # Celery worker (Phase 5) — dedicated broker Redis with `noeviction`:
    # the main cache Redis runs allkeys-lru, which may silently drop queued
    # task messages, so the broker gets its own tiny instance.
    celery_broker_url: str = "redis://localhost:6380/0"
    celery_result_backend: str = "redis://localhost:6380/1"
    # Champion model artifacts (exported by packages/ml training, baked into
    # the worker image — MLflow itself never runs on the VPS, docs/02).
    model_dir: str = "/app/models"
    forecast_horizon_days: int = 14


@lru_cache
def get_settings() -> Settings:
    return Settings()

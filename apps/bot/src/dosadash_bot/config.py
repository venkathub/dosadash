"""Bot settings from env vars (compose passes TELEGRAM_BOT_TOKEN from infra/.env)."""

import hashlib
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str
    public_base_url: str = "https://dosadash.venkateshs.dev"
    webhook_path: str = "/tg/webhook"
    port: int = 8081
    # bot → api internal calls (docker network)
    api_base_url: str = "http://api:8000"
    internal_api_token: str = ""
    # Optional explicit secret; defaults to a token-derived value so the
    # webhook endpoint always rejects posts that aren't from Telegram.
    telegram_webhook_secret: str = ""

    @property
    def webhook_secret(self) -> str:
        if self.telegram_webhook_secret:
            return self.telegram_webhook_secret
        return hashlib.sha256(f"dosadash:{self.telegram_bot_token}".encode()).hexdigest()[:32]

    @property
    def webhook_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.webhook_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Service settings loaded from environment variables (never hardcode secrets)."""

import re
from functools import lru_cache
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AI service settings. All values come from env vars / .env.

    LLM keys are consumed by litellm (Hard Rule 1: never call provider SDKs
    directly); they are declared here so misconfiguration fails fast.
    """

    model_config = SettingsConfigDict(env_prefix="AI_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://dosadash:dosadash@localhost:5432/dosadash"
    redis_url: str = "redis://localhost:6379/0"

    # Shared secret for api→ai internal calls (same pattern as bot→api).
    internal_api_token: str = ""

    # litellm routing chain (docs/02): primary → fast tier → fallback.
    # Provider keys (OPENAI_API_KEY, GROQ_API_KEY, GEMINI_API_KEY) are read by
    # litellm straight from the environment — never call provider SDKs directly.
    # Groq decommissioned openai/gpt-oss-120b on 2026-08-16 (caught live
    # by the eval gate); openai/gpt-oss-120b is Groq's recommended successor.
    llm_models: list[str] = [
        "gpt-4o-mini",
        "groq/openai/gpt-oss-120b",
        "gemini/gemini-1.5-flash",
    ]

    # STT (Phase 7): Groq Whisper via litellm — API inference only, never a
    # local Whisper on the 4 GB VPS (Hard Rule 7). No fallback: Groq is the
    # only STT-capable provider in the stack; failures degrade to "please
    # type" in the bot.
    stt_model: str = "groq/whisper-large-v3"

    # RAG (Phase 3): embeddings via litellm; dimension pinned by the
    # vector(1536) columns (dosadash_shared.EMBEDDING_DIM).
    embedding_model: str = "text-embedding-3-small"
    # Path to the knowledge/ markdown sources (repo checkout or baked into
    # the container image). Used by the ingestion CLI and re-embed cascade.
    knowledge_dir: str = "knowledge"

    # Semantic cache (Phase 4, docs/02+06): Redis `semcache:*`, Q&A only.
    semcache_enabled: bool = True
    semcache_threshold: float = 0.95  # cosine — docs/06
    semcache_ttl_seconds: int = 86400
    semcache_max_candidates: int = 128  # bounded in-process scoring

    # Champion ML artifacts (Phase 5): exported by packages/ml training and
    # baked into the image; ETA scoring loads {model_dir}/eta/champion.
    model_dir: str = "packages/ml/artifacts"

    # Analytics copilot (Phase 5): when a read-only role password is set
    # (role created by api migration b3f9c82d4e61), copilot queries run as
    # that role — DB-level enforcement behind the SQL guardrail.
    readonly_db_user: str = "dosadash_readonly"
    readonly_db_password: str = ""

    @property
    def copilot_database_url(self) -> str:
        """Read-only-role DSN, or '' → fall back to the main URL (still
        guarded by validation + READ ONLY transaction)."""
        if not self.readonly_db_password:
            return ""
        creds = f"{self.readonly_db_user}:{quote(self.readonly_db_password, safe='')}"
        return re.sub(r"//[^@/]+@", f"//{creds}@", self.database_url, count=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()

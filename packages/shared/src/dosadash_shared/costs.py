"""LLM cost dashboard schemas (Phase 4 LLMOps).

Costs come from Langfuse's daily metrics API (every LLM call is traced
there with token usage and computed cost — Hard Rule 6 pays off). The AI
service owns the Langfuse keys and normalizes the payload; the core API
proxies it to the admin surface (RBAC) untouched.
"""

from pydantic import BaseModel, Field


class ModelDailyCost(BaseModel):
    """One model's spend on one day."""

    model: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0  # observations (LLM generations)


class DailyCost(BaseModel):
    date: str  # YYYY-MM-DD
    traces: int = 0
    observations: int = 0
    cost_usd: float = 0.0
    models: list[ModelDailyCost] = Field(default_factory=list)


class CostSummaryResponse(BaseModel):
    """Daily spend, newest day first. `configured` is false when Langfuse
    keys are absent — the dashboard shows setup guidance instead of zeros."""

    configured: bool = True
    days: list[DailyCost] = Field(default_factory=list)
    total_cost_usd: float = 0.0


class SemcacheStats(BaseModel):
    """Semantic-cache counters (Phase 9): lookups on the RAG Q&A path.
    Running indicators, not billing records — an LRU eviction may reset them."""

    exact_hits: int = 0
    semantic_hits: int = 0
    misses: int = 0
    stores: int = 0
    flushes: int = 0
    lookups: int = 0  # exact + semantic + misses (errors excluded — unknown outcome)
    hit_rate: float = 0.0  # (exact + semantic) / lookups, 0 when no lookups


class PromptCacheStats(BaseModel):
    """Provider prompt-cache counters: how much of our prefix-stable message
    layout is actually being served from the provider's cache."""

    calls: int = 0
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_share: float = 0.0  # cached / prompt tokens, 0 when no prompt tokens


class CacheStatsResponse(BaseModel):
    """Cache observability rollup for the admin Costs tab (Phase 9)."""

    semcache: SemcacheStats = Field(default_factory=SemcacheStats)
    prompt_cache: PromptCacheStats = Field(default_factory=PromptCacheStats)
    semcache_enabled: bool = True
    semcache_threshold: float = 0.95
    semcache_ttl_seconds: int = 86400

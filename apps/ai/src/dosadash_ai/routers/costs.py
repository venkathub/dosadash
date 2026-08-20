"""Internal cost endpoint (Phase 4 LLMOps): Langfuse daily metrics.

GET /internal/costs/daily — X-Internal-Token guarded (api → ai).

Every LLM call is traced to Langfuse with tokens + computed cost (Hard
Rule 6); this endpoint pulls the daily rollup from Langfuse's public
metrics API and normalizes it to `CostSummaryResponse`. A short
in-process TTL cache keeps admin-tab refreshes from hammering Langfuse.
"""

import logging
import os
import secrets
import time
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Query

from dosadash_ai.config import get_settings
from dosadash_ai.llm.semcache import get_semcache
from dosadash_ai.llm.usage_stats import get_usage_stats
from dosadash_shared import (
    CacheStatsResponse,
    CostSummaryResponse,
    DailyCost,
    ModelDailyCost,
    PromptCacheStats,
    SemcacheStats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/costs", tags=["internal:costs"])

_CACHE_TTL_SECONDS = 60.0
_cache: dict[int, tuple[float, CostSummaryResponse]] = {}


def _check_internal_token(provided: str) -> None:
    expected = get_settings().internal_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def _langfuse_auth() -> tuple[str, str, str] | None:
    """(host, public_key, secret_key) or None when tracing isn't configured.
    Read from os.environ like configure_tracing() — litellm uses the same vars."""
    public = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not public or not secret:
        return None
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/")
    return host, public, secret


async def _fetch_daily_metrics(host: str, auth: tuple[str, str], days: int) -> dict[str, Any]:
    """One page of Langfuse's daily metrics API (newest first)."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{host}/api/public/metrics/daily", params={"limit": days}, auth=auth
        )
    resp.raise_for_status()
    return resp.json()


def _normalize(payload: dict[str, Any]) -> list[DailyCost]:
    days: list[DailyCost] = []
    for row in payload.get("data", []):
        models = [
            ModelDailyCost(
                model=usage.get("model") or "unknown",
                cost_usd=float(usage.get("totalCost") or 0.0),
                input_tokens=int(usage.get("inputUsage") or 0),
                output_tokens=int(usage.get("outputUsage") or 0),
                calls=int(usage.get("countObservations") or 0),
            )
            for usage in row.get("usage", [])
        ]
        days.append(
            DailyCost(
                date=str(row.get("date", "")),
                traces=int(row.get("countTraces") or 0),
                observations=int(row.get("countObservations") or 0),
                cost_usd=float(row.get("totalCost") or 0.0),
                models=models,
            )
        )
    return days


@router.get("/daily", response_model=CostSummaryResponse)
async def daily_costs(
    x_internal_token: Annotated[str, Header()] = "",
    days: Annotated[int, Query(ge=1, le=60)] = 30,
) -> CostSummaryResponse:
    _check_internal_token(x_internal_token)

    creds = _langfuse_auth()
    if creds is None:
        return CostSummaryResponse(configured=False)

    cached = _cache.get(days)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    host, public, secret = creds
    try:
        payload = await _fetch_daily_metrics(host, (public, secret), days)
    except httpx.HTTPError as exc:
        logger.warning("costs: Langfuse fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail="Cost provider unavailable") from exc

    day_rows = _normalize(payload)
    response = CostSummaryResponse(
        configured=True,
        days=day_rows,
        total_cost_usd=round(sum(d.cost_usd for d in day_rows), 6),
    )
    _cache[days] = (time.monotonic(), response)
    return response


@router.get("/cache", response_model=CacheStatsResponse)
async def cache_stats(x_internal_token: Annotated[str, Header()] = "") -> CacheStatsResponse:
    """Cache observability (Phase 9): semantic-cache hit rate + provider
    prompt-cache token share. Counters are running indicators on the cache
    Redis (LRU eviction may reset them); billing truth stays in Langfuse."""
    _check_internal_token(x_internal_token)
    settings = get_settings()

    sem_raw = await get_semcache().stats()
    exact = sem_raw.get("exact_hits", 0)
    semantic = sem_raw.get("semantic_hits", 0)
    misses = sem_raw.get("misses", 0)
    lookups = exact + semantic + misses
    semcache = SemcacheStats(
        exact_hits=exact,
        semantic_hits=semantic,
        misses=misses,
        stores=sem_raw.get("stores", 0),
        flushes=sem_raw.get("flushes", 0),
        lookups=lookups,
        hit_rate=round((exact + semantic) / lookups, 4) if lookups else 0.0,
    )

    prompt_raw = await get_usage_stats().snapshot()
    prompt_tokens = prompt_raw.get("prompt_tokens", 0)
    cached = prompt_raw.get("cached_prompt_tokens", 0)
    prompt_cache = PromptCacheStats(
        calls=prompt_raw.get("calls", 0),
        prompt_tokens=prompt_tokens,
        cached_prompt_tokens=cached,
        completion_tokens=prompt_raw.get("completion_tokens", 0),
        cached_share=round(cached / prompt_tokens, 4) if prompt_tokens else 0.0,
    )

    return CacheStatsResponse(
        semcache=semcache,
        prompt_cache=prompt_cache,
        semcache_enabled=settings.semcache_enabled,
        semcache_threshold=settings.semcache_threshold,
        semcache_ttl_seconds=settings.semcache_ttl_seconds,
    )

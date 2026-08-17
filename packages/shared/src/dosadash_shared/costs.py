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

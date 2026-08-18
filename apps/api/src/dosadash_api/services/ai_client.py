"""Thin HTTP client for the AI service (api → ai internal calls).

Injectable as a FastAPI dependency so tests can substitute a fake without
touching the network. Auth mirrors bot→api: shared X-Internal-Token.
"""

import httpx

from dosadash_api.config import get_settings
from dosadash_shared import (
    CopilotAnswer,
    CopilotAskIn,
    CostSummaryResponse,
    EtaRequest,
    EtaResponse,
    InventoryDraftRequest,
    InventoryDraftResult,
    NutritionEstimateRequest,
    NutritionEstimateResponse,
)


class AIServiceError(Exception):
    """The AI service was unreachable or returned an error."""


class AIClient:
    def __init__(self, base_url: str, internal_token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = internal_token

    async def estimate_nutrition(
        self, request: NutritionEstimateRequest
    ) -> NutritionEstimateResponse:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/nutrition/estimate",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return NutritionEstimateResponse.model_validate(resp.json())

    async def predict_eta(self, request: EtaRequest) -> EtaResponse:
        """Checkout-time ETA. Short timeout: caller falls back to a heuristic."""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/eta",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return EtaResponse.model_validate(resp.json())

    async def copilot_ask(self, request: CopilotAskIn, *, admin_user_id: int) -> CopilotAnswer:
        """Analytics copilot: LLM SQL draft + self-correction can take a while."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/copilot/ask",
                    json=request.model_dump(mode="json"),
                    headers={
                        "X-Internal-Token": self._token,
                        "X-Admin-User-Id": str(admin_user_id),
                    },
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return CopilotAnswer.model_validate(resp.json())

    async def draft_inventory_pos(self, request: InventoryDraftRequest) -> InventoryDraftResult:
        """Inventory agent (Phase 6): needs math + LLM pass + guardrail."""
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/inventory/draft-po",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return InventoryDraftResult.model_validate(resp.json())

    async def daily_costs(self, days: int = 30) -> CostSummaryResponse:
        """LLM spend rollup (ai → Langfuse). Raises AIServiceError on failure."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{self._base_url}/internal/costs/daily",
                    params={"days": days},
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return CostSummaryResponse.model_validate(resp.json())


def get_ai_client() -> AIClient:
    s = get_settings()
    return AIClient(base_url=s.ai_base_url, internal_token=s.internal_api_token)

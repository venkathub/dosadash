"""Thin HTTP client for the AI service (api → ai internal calls).

Injectable as a FastAPI dependency so tests can substitute a fake without
touching the network. Auth mirrors bot→api: shared X-Internal-Token.
"""

import httpx

from dosadash_api.config import get_settings
from dosadash_shared import NutritionEstimateRequest, NutritionEstimateResponse


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


def get_ai_client() -> AIClient:
    s = get_settings()
    return AIClient(base_url=s.ai_base_url, internal_token=s.internal_api_token)

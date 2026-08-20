"""Thin HTTP client for the AI service (api → ai internal calls).

Injectable as a FastAPI dependency so tests can substitute a fake without
touching the network. Auth mirrors bot→api: shared X-Internal-Token.
"""

import httpx

from dosadash_api.config import get_settings
from dosadash_shared import (
    CheckoutSuggestResponse,
    CopilotAnswer,
    CopilotAskIn,
    CostSummaryResponse,
    DishQCIn,
    DishQCResult,
    EtaRequest,
    EtaResponse,
    InventoryDraftRequest,
    InventoryDraftResult,
    InvoiceExtractIn,
    InvoiceExtractResult,
    MenuImageRequest,
    MenuImageResult,
    MenuTranslationRequest,
    MenuTranslationResponse,
    NutritionEstimateRequest,
    NutritionEstimateResponse,
    PromoSuggestResult,
    RecsRequest,
    RecsResponse,
    ReviewReplyRequest,
    ReviewReplyResponse,
    ReviewScoreRequest,
    ReviewScoreResponse,
    SupportAgentRequest,
    SupportAgentResponse,
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

    async def extract_invoice(self, request: InvoiceExtractIn) -> InvoiceExtractResult:
        """Supplier invoice OCR (Phase 6): VLM extraction + arithmetic checks."""
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/invoice/extract",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return InvoiceExtractResult.model_validate(resp.json())

    async def qc_dish(self, request: DishQCIn) -> DishQCResult:
        """Dish-photo QC (Phase 7): VLM observations + deterministic verdict."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/qc/dish",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return DishQCResult.model_validate(resp.json())

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

    async def support_chat(self, request: SupportAgentRequest) -> SupportAgentResponse:
        """Support agent (Phase 6): one guarded reasoning turn."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/support/chat",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return SupportAgentResponse.model_validate(resp.json())

    async def recommend(self, request: RecsRequest) -> RecsResponse:
        """Recommendations (Phase 7). Short timeout: the menu page renders
        fine without them — the caller degrades to an empty strip."""
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/recs",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return RecsResponse.model_validate(resp.json())

    async def suggest_promos(self, *, admin_user_id: int) -> PromoSuggestResult:
        """Promo agent (Phase 7): mined candidates + LLM copy + guardrail."""
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/promo/suggest",
                    headers={
                        "X-Internal-Token": self._token,
                        "X-Admin-User-Id": str(admin_user_id),
                    },
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return PromoSuggestResult.model_validate(resp.json())

    async def suggest_checkout(self, request: RecsRequest) -> CheckoutSuggestResponse:
        """Checkout add-on suggestions (Phase 7). Same degrade contract as
        recommend — checkout must never block on suggestions."""
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/recs/checkout",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return CheckoutSuggestResponse.model_validate(resp.json())

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

    async def translate_menu(self, request: MenuTranslationRequest) -> MenuTranslationResponse:
        """Menu localization drafts (Phase 7, Tamil-first). Long timeout —
        the ai side fans one request out over several chunked LLM calls."""
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/translate/menu",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return MenuTranslationResponse.model_validate(resp.json())

    async def generate_menu_image(self, request: MenuImageRequest) -> MenuImageResult:
        """AI dish photo draft (Phase 7). Image models are slow — long timeout;
        single provider ai-side, so failure comes back as one clean error."""
        try:
            async with httpx.AsyncClient(timeout=150) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/imagegen/menu-item",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return MenuImageResult.model_validate(resp.json())

    async def score_reviews(self, request: ReviewScoreRequest) -> ReviewScoreResponse:
        """Aspect-sentiment tagging (Phase 8). Long timeout — the ai side
        fans one request out over several chunked LLM calls."""
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/reviews/score",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return ReviewScoreResponse.model_validate(resp.json())

    async def draft_review_reply(self, request: ReviewReplyRequest) -> ReviewReplyResponse:
        """AI-drafted owner reply (Phase 8) — never fails business flow:
        the ai side falls back to a deterministic template on LLM failure."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/reviews/draft-reply",
                    json=request.model_dump(mode="json"),
                    headers={"X-Internal-Token": self._token},
                )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AIServiceError(f"AI service call failed: {exc}") from exc
        return ReviewReplyResponse.model_validate(resp.json())


def get_ai_client() -> AIClient:
    s = get_settings()
    return AIClient(base_url=s.ai_base_url, internal_token=s.internal_api_token)

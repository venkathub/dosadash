"""FastAPI entrypoint for the AI service."""

from fastapi import FastAPI

from dosadash_ai.llm import configure_tracing
from dosadash_ai.routers.nutrition import router as nutrition_router
from dosadash_ai.routers.rag import router as rag_router
from dosadash_shared import HealthStatus

configure_tracing()  # Langfuse callback when keys are present (Hard Rule 6)

app = FastAPI(title="DosaDash AI", version="0.1.0")
app.include_router(nutrition_router)
app.include_router(rag_router)


@app.get("/healthz", response_model=HealthStatus)
async def healthz() -> HealthStatus:
    """Liveness probe."""
    return HealthStatus(service="ai")

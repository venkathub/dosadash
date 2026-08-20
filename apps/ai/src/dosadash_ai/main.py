"""FastAPI entrypoint for the AI service."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from dosadash_ai.config import get_settings
from dosadash_ai.llm import configure_tracing
from dosadash_ai.routers.agent import router as agent_router
from dosadash_ai.routers.copilot import router as copilot_router
from dosadash_ai.routers.costs import router as costs_router
from dosadash_ai.routers.eta import router as eta_router
from dosadash_ai.routers.inventory import router as inventory_router
from dosadash_ai.routers.invoice import router as invoice_router
from dosadash_ai.routers.nutrition import router as nutrition_router
from dosadash_ai.routers.qc import router as qc_router
from dosadash_ai.routers.rag import router as rag_router
from dosadash_ai.routers.support import router as support_router
from dosadash_shared import HealthStatus

logger = logging.getLogger(__name__)

configure_tracing()  # Langfuse callback when keys are present (Hard Rule 6)


async def _startup_knowledge_ingest() -> None:
    """Best-effort re-embed of knowledge/ at boot (hash-diffed → ~free when
    unchanged). Failure degrades RAG freshness, never service availability."""
    from dosadash_ai.db import get_sessionmaker
    from dosadash_ai.rag.ingest import ingest_knowledge_dir

    directory = Path(get_settings().knowledge_dir)
    if not await asyncio.to_thread(directory.is_dir):
        logger.warning("knowledge ingest skipped: %s not found", directory)
        return
    try:
        async with get_sessionmaker()() as session:
            report = await ingest_knowledge_dir(session, directory)
        logger.info("startup %s", report)
    except Exception:  # noqa: BLE001 — best-effort by design
        logger.warning("startup knowledge ingest failed", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    from dosadash_ai.cascade import run_menu_listener

    tasks = [
        asyncio.create_task(_startup_knowledge_ingest(), name="knowledge-ingest"),
        asyncio.create_task(run_menu_listener(), name="menu-cascade"),  # Hard Rule 4
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="DosaDash AI", version="0.1.0", lifespan=lifespan)
app.include_router(nutrition_router)
app.include_router(rag_router)
app.include_router(agent_router)
app.include_router(copilot_router)
app.include_router(costs_router)
app.include_router(eta_router)
app.include_router(inventory_router)
app.include_router(invoice_router)
app.include_router(support_router)
app.include_router(qc_router)


@app.get("/healthz", response_model=HealthStatus)
async def healthz() -> HealthStatus:
    """Liveness probe."""
    return HealthStatus(service="ai")

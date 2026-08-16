"""FastAPI entrypoint for the AI service."""

from fastapi import FastAPI

from dosadash_shared import HealthStatus

app = FastAPI(title="DosaDash AI", version="0.1.0")


@app.get("/healthz", response_model=HealthStatus)
async def healthz() -> HealthStatus:
    """Liveness probe."""
    return HealthStatus(service="ai")

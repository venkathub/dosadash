"""FastAPI entrypoint for the core API service."""

from fastapi import FastAPI

from dosadash_shared import HealthStatus

app = FastAPI(title="DosaDash API", version="0.1.0")


@app.get("/healthz", response_model=HealthStatus)
async def healthz() -> HealthStatus:
    """Liveness probe."""
    return HealthStatus(service="api")

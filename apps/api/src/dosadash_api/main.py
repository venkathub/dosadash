"""FastAPI entrypoint for the core API service."""

from fastapi import FastAPI

from dosadash_api.routers.auth import router as auth_router
from dosadash_api.routers.menu import router as menu_router
from dosadash_api.routers.orders import router as orders_router
from dosadash_api.routers.payments import router as payments_router
from dosadash_api.routers.profile import router as profile_router
from dosadash_api.routers.ws import router as ws_router
from dosadash_shared import HealthStatus

app = FastAPI(title="DosaDash API", version="0.1.0")
app.include_router(auth_router)
app.include_router(menu_router)
app.include_router(orders_router)
app.include_router(payments_router)
app.include_router(profile_router)
app.include_router(ws_router)


@app.get("/healthz", response_model=HealthStatus)
async def healthz() -> HealthStatus:
    """Liveness probe."""
    return HealthStatus(service="api")

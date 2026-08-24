"""FastAPI entrypoint for the core API service."""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from dosadash_api.config import get_settings
from dosadash_api.ratelimit import RateLimitMiddleware
from dosadash_api.routers.admin_combos import router as admin_combos_router
from dosadash_api.routers.admin_copilot import router as admin_copilot_router
from dosadash_api.routers.admin_costs import router as admin_costs_router
from dosadash_api.routers.admin_coupons import router as admin_coupons_router
from dosadash_api.routers.admin_crm import router as admin_crm_router
from dosadash_api.routers.admin_evals import router as admin_evals_router
from dosadash_api.routers.admin_feedback import router as admin_feedback_router
from dosadash_api.routers.admin_ingredients import router as admin_ingredients_router
from dosadash_api.routers.admin_inventory import router as admin_inventory_router
from dosadash_api.routers.admin_invoices import router as admin_invoices_router
from dosadash_api.routers.admin_menu import router as admin_menu_router
from dosadash_api.routers.admin_menu_images import router as admin_menu_images_router
from dosadash_api.routers.admin_nutrition import router as admin_nutrition_router
from dosadash_api.routers.admin_ops import router as admin_ops_router
from dosadash_api.routers.admin_orders import router as admin_orders_router
from dosadash_api.routers.admin_promos import router as admin_promos_router
from dosadash_api.routers.admin_reports import router as admin_reports_router
from dosadash_api.routers.admin_reviews import router as admin_reviews_router
from dosadash_api.routers.admin_suppliers import router as admin_suppliers_router
from dosadash_api.routers.admin_translations import router as admin_translations_router
from dosadash_api.routers.admin_wastage import router as admin_wastage_router
from dosadash_api.routers.aggregator import router as aggregator_router
from dosadash_api.routers.auth import router as auth_router
from dosadash_api.routers.chat import router as chat_router
from dosadash_api.routers.coupons import router as coupons_router
from dosadash_api.routers.feedback import router as feedback_router
from dosadash_api.routers.internal_mcp import router as internal_mcp_router
from dosadash_api.routers.menu import router as menu_router
from dosadash_api.routers.orders import router as orders_router
from dosadash_api.routers.payments import router as payments_router
from dosadash_api.routers.profile import router as profile_router
from dosadash_api.routers.recs import router as recs_router
from dosadash_api.routers.reviews import router as reviews_router
from dosadash_api.routers.support import router as support_router
from dosadash_api.routers.ws import router as ws_router
from dosadash_shared import HealthStatus

app = FastAPI(title="DosaDash API", version="0.1.0")
# Phase 9 hardening: inbound rate limiting (pure ASGI — SSE-safe, fail-open).
app.add_middleware(RateLimitMiddleware)
app.include_router(auth_router)
app.include_router(aggregator_router)
app.include_router(admin_combos_router)
app.include_router(admin_copilot_router)
app.include_router(admin_costs_router)
app.include_router(admin_coupons_router)
app.include_router(admin_crm_router)
app.include_router(admin_evals_router)
app.include_router(admin_feedback_router)
app.include_router(admin_ingredients_router)
app.include_router(admin_inventory_router)
app.include_router(admin_invoices_router)
app.include_router(admin_menu_router)
app.include_router(admin_menu_images_router)
app.include_router(admin_nutrition_router)
app.include_router(admin_ops_router)
app.include_router(admin_orders_router)
app.include_router(admin_promos_router)
app.include_router(admin_reports_router)
app.include_router(admin_reviews_router)
app.include_router(admin_suppliers_router)
app.include_router(admin_translations_router)
app.include_router(admin_wastage_router)
app.include_router(chat_router)
app.include_router(coupons_router)
app.include_router(feedback_router)
app.include_router(internal_mcp_router)
app.include_router(menu_router)
app.include_router(orders_router)
app.include_router(payments_router)
app.include_router(profile_router)
app.include_router(recs_router)
app.include_router(reviews_router)
app.include_router(support_router)
app.include_router(ws_router)

# AI dish photos (Phase 7): owner-approved files served from the media dir
# (named volume in compose; Caddy routes /media/* here). Media is a
# nice-to-have — a broken volume mount must degrade to "no photos", never
# take checkout down (learned in prod: a root-owned fresh volume denied
# mkdir and crash-looped the whole api).
_media_root = Path(get_settings().media_dir)
try:
    (_media_root / "menu").mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=_media_root), name="media")
except OSError:
    logging.getLogger(__name__).exception(
        "media dir %s unusable — /media disabled, API continues without photos", _media_root
    )


@app.get("/healthz", response_model=HealthStatus)
async def healthz() -> HealthStatus:
    """Liveness probe."""
    return HealthStatus(service="api")

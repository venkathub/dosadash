# Build context = repo root (workspace install needs packages/shared)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
COPY packages/shared/ packages/shared/
COPY packages/ml/pyproject.toml packages/ml/pyproject.toml
COPY packages/ml/src/ packages/ml/src/
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/ai/pyproject.toml apps/ai/pyproject.toml
COPY apps/bot/pyproject.toml apps/bot/pyproject.toml
COPY apps/bot/src/ apps/bot/src/
COPY apps/ai/src/ apps/ai/src/
COPY apps/api/src/ apps/api/src/
COPY apps/api/alembic.ini apps/api/alembic.ini
COPY apps/api/migrations/ apps/api/migrations/
# Champion ML artifacts (exported by packages/ml training; scored nightly by
# the worker). Tiny (~1 MB) — harmless in the api image, same layer cache.
COPY packages/ml/artifacts/ models/
# "" for the api service; "--extra worker" for the celery worker service
ARG UV_EXTRA_ARGS=""
RUN uv sync --frozen --no-dev --package dosadash-api ${UV_EXTRA_ARGS}

FROM python:3.12-slim-bookworm
WORKDIR /app
RUN useradd --create-home appuser
COPY --from=builder --chown=appuser:appuser /app /app
# Media dir owned by appuser at build time: a FRESH named volume mounted at
# /app/media inherits this ownership. (Phase 7 postmortem: a root-owned
# volume crash-looped the api on mkdir — main.py now also degrades instead
# of dying, but correct ownership is the real fix.)
RUN mkdir -p /app/media/menu && chown -R appuser:appuser /app/media
USER appuser
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
# Run pending migrations, then serve.
CMD ["sh", "-c", "cd apps/api && alembic upgrade head && exec uvicorn dosadash_api.main:app --host 0.0.0.0 --port 8000"]

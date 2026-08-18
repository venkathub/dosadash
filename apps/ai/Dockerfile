# Build context = repo root (workspace install needs packages/shared)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
COPY packages/shared/ packages/shared/
COPY packages/ml/pyproject.toml packages/ml/pyproject.toml
COPY packages/ml/src/ packages/ml/src/
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/api/src/ apps/api/src/
COPY apps/bot/pyproject.toml apps/bot/pyproject.toml
COPY apps/bot/src/ apps/bot/src/
COPY apps/ai/pyproject.toml apps/ai/pyproject.toml
COPY apps/ai/src/ apps/ai/src/
COPY apps/ai/prompts/ apps/ai/prompts/
COPY knowledge/ knowledge/
# Champion ML artifacts (ETA scoring at checkout — Phase 5)
COPY packages/ml/artifacts/ models/
RUN uv sync --frozen --no-dev --package dosadash-ai

FROM python:3.12-slim-bookworm
WORKDIR /app
RUN useradd --create-home appuser
COPY --from=builder --chown=appuser:appuser /app /app
USER appuser
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/healthz')"
CMD ["uvicorn", "dosadash_ai.main:app", "--host", "0.0.0.0", "--port", "8001"]

#!/bin/bash
# Run the Phase 3 live eval suites against an isolated local database.
#
#   cp .env.example .env.eval   # fill in keys (file is gitignored)
#   bash scripts/run-live-evals.sh
#
# Steps: create dosadash_eval DB (idempotent) → alembic upgrade head →
# seed menu/users → ingest knowledge/ → retrieval_eval → rag_answer_eval →
# order_agent_eval. Exits non-zero if any suite fails its threshold.

set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="${1:-.env.eval}"
if [ ! -f "$ENV_FILE" ]; then
  echo "✗ $ENV_FILE not found — cp .env.example .env.eval and fill in keys." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${OPENAI_API_KEY:?OPENAI_API_KEY is required in $ENV_FILE}"

EVAL_DATABASE_URL="${EVAL_DATABASE_URL:-postgresql+asyncpg://dosadash:dosadash@localhost:5433/dosadash_eval}"
export EVAL_DATABASE_URL
export AI_DATABASE_URL="$EVAL_DATABASE_URL"
export API_DATABASE_URL="$EVAL_DATABASE_URL"
export AI_KNOWLEDGE_DIR="knowledge"

echo "── eval DB: $EVAL_DATABASE_URL"

echo "── [1/6] ensure database exists"
uv run python - <<'PY'
import asyncio, os, asyncpg
url = os.environ["EVAL_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
base, _, dbname = url.rpartition("/")
async def main():
    conn = await asyncpg.connect(f"{base}/postgres")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{dbname}"')
            print(f"created database {dbname}")
        else:
            print(f"database {dbname} already exists")
    finally:
        await conn.close()
asyncio.run(main())
PY

echo "── [2/6] migrations"
(cd apps/api && uv run alembic upgrade head)

echo "── [3/6] seed menu + synthetic users/orders (skips if already seeded)"
uv run python -m dosadash_api.seed

echo "── [4/6] ingest knowledge/ (hash-diffed — cheap when unchanged)"
uv run python -m dosadash_ai.rag.ingest --knowledge-dir knowledge

failures=()
run_suite() {
  echo
  echo "══════════════════════ $1 ══════════════════════"
  if ! uv run python "evals/suites/$1"; then
    failures+=("$1")
  fi
}

echo "── [5/6] RAG suites"
run_suite retrieval_eval.py
run_suite rag_answer_eval.py

echo "── [6/6] order agent suite (order_accuracy)"
run_suite order_agent_eval.py

echo
if [ "${#failures[@]}" -gt 0 ]; then
  echo "✗ FAILED suites: ${failures[*]}"
  exit 1
fi
echo "✓ all live eval suites passed — verify traces in Langfuse, then open the Phase PR."

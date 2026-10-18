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

export AI_SEMCACHE_ENABLED=false
uv run --no-sync python - <<'PYGUARD'
import sys
sys.path.insert(0, "evals/suites")
from _results import require_eval_database
require_eval_database()
print("Validated dedicated local evaluation database")
PYGUARD

echo "── [1/6] ensure database exists"
uv run --no-sync python - <<'PY'
import asyncio, os, asyncpg
url = os.environ["EVAL_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
base, _, dbname = url.rpartition("/")
async def main():
    conn = await asyncpg.connect(f"{base}/postgres")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            quoted_name = dbname.replace(chr(34), chr(34) * 2)
            await conn.execute(f'CREATE DATABASE "{quoted_name}"')
            print(f"created database {dbname}")
        else:
            print(f"database {dbname} already exists")
    finally:
        await conn.close()
asyncio.run(main())
PY

echo "── [2/6] migrations"
(cd apps/api && uv run --no-sync alembic upgrade head)

echo "── [3/6] seed menu + synthetic users/orders (skips if already seeded)"
uv run --no-sync python -m dosadash_api.seed --end-date 2026-08-20 --users 40

echo "── [4/6] ingest knowledge/ (hash-diffed — cheap when unchanged)"
uv run --no-sync python -m dosadash_ai.rag.ingest --knowledge-dir knowledge

echo "── run combined suites (ordering, retrieval, RAG, support, inventory, SQL, tone)"
uv run --no-sync python evals/suites/run_live_evals.py --profile "${EVAL_PROFILE:-pr}" \
  --json "${EVAL_RESULTS_PATH:-live_eval_results.json}"

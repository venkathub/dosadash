"""Live zero-shot review-scoring benchmark — the API side of the
LoRA-vs-zero-shot comparison (Phase 8).

Runs the PRODUCTION scoring path (review_sentiment_v1 prompt +
sanitize_scores guardrail, chunked like serving) over a deterministic
sample of the fine-tune TEST split, scores it with the SAME shared metrics
used for the LoRA model, estimates cost from token counts, and writes
`packages/ml/artifacts/sentiment/zero_shot.json` for benchmark.py to merge.

Requires real LLM keys; NOT run in CI (CI gates the committed artifact).

Usage:
    set -a; source .env.eval; set +a
    uv run python evals/suites/review_zero_shot_eval.py           # N=250
    ZS_SAMPLE=100 uv run python evals/suites/review_zero_shot_eval.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import litellm

from dosadash_ai.llm import LLMError, structured_completion
from dosadash_ai.routers.reviews import build_score_messages, sanitize_scores
from dosadash_ml.finetune.dataset import build_examples
from dosadash_ml.finetune.metrics import label_set_metrics
from dosadash_shared import (
    REVIEW_SCORE_CHUNK_SIZE,
    REVIEW_SENTIMENT_PROMPT_VERSION,
    ReviewScoreBatch,
    ReviewScoreSourceItem,
)

SAMPLE = int(os.environ.get("ZS_SAMPLE", "250"))
OUT = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "ml"
    / "artifacts"
    / "sentiment"
    / "zero_shot.json"
)
# published gpt-4o-mini prices (USD per 1M tokens) + a fixed demo fx rate;
# recorded in the artifact so the economics stay auditable
PRICE_IN_PER_M = 0.15
PRICE_OUT_PER_M = 0.60
INR_PER_USD = 88.0


async def run() -> int:
    test = [e for e in build_examples() if e.split == "test"][:SAMPLE]
    print(f"zero-shot benchmark: {len(test)} holdout reviews (of the shared test split)")

    preds: list[set[str]] = []
    models_used: set[str] = set()
    in_tokens = out_tokens = 0
    started = time.time()
    for start in range(0, len(test), REVIEW_SCORE_CHUNK_SIZE):
        chunk = test[start : start + REVIEW_SCORE_CHUNK_SIZE]
        requested = [
            ReviewScoreSourceItem(review_id=start + j, rating=e.rating, text=e.text)
            for j, e in enumerate(chunk)
        ]
        messages = build_score_messages(requested)
        try:
            parsed, model = await structured_completion(
                messages=messages,
                response_model=ReviewScoreBatch,
                trace_name="evals.review_zero_shot",
                prompt_version=REVIEW_SENTIMENT_PROMPT_VERSION,
                session_id="evals:review-zero-shot",
                max_tokens=2000,
            )
        except LLMError as exc:
            print(f"chunk @{start} failed: {exc}", file=sys.stderr)
            preds.extend(set() for _ in chunk)
            continue
        models_used.add(model)
        kept, _ = sanitize_scores(requested, parsed)
        by_id = {s.review_id: s for s in kept}
        for j in range(len(chunk)):
            score = by_id.get(start + j)
            preds.append({f"{a.aspect}:{a.sentiment}" for a in score.aspects} if score else set())
        raw = parsed.model_dump_json()
        in_tokens += litellm.token_counter(model="gpt-4o-mini", messages=messages)
        out_tokens += litellm.token_counter(model="gpt-4o-mini", text=raw)
        print(f"  {start + len(chunk)}/{len(test)} scored via {model}")

    seconds = time.time() - started
    metrics = label_set_metrics(preds, test)
    cost_usd = (in_tokens * PRICE_IN_PER_M + out_tokens * PRICE_OUT_PER_M) / 1_000_000
    cost_inr_per_1k = cost_usd / len(test) * 1000 * INR_PER_USD
    artifact = {
        "prompt_version": REVIEW_SENTIMENT_PROMPT_VERSION,
        "models_used": sorted(models_used),
        "sample": len(test),
        "metrics": metrics,
        "est_input_tokens": in_tokens,
        "est_output_tokens": out_tokens,
        "price_usd_per_m": {"input": PRICE_IN_PER_M, "output": PRICE_OUT_PER_M},
        "inr_per_usd": INR_PER_USD,
        "cost_inr_per_1k_reviews": round(cost_inr_per_1k, 2),
        "wall_seconds": round(seconds, 1),
        "predictions": [sorted(p) for p in preds],  # index-aligned with the test-split prefix
        "ran_at": datetime.now(UTC).isoformat(),
        "methodology": (
            "production prompt+guardrail path on the shared fine-tune test split; "
            "cost estimated from litellm token counts at published gpt-4o-mini prices"
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(artifact, indent=2) + "\n")
    print(json.dumps({k: v for k, v in artifact.items() if k != "predictions"}, indent=2))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))

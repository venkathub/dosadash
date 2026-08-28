# 07 — Resume Bullets (real measured numbers — sources in parentheses)

All numbers below are measured and reproducible: eval-gate runs are recorded in the
`eval_runs` table (admin scoreboard), ML numbers live in committed benchmark
artifacts under `packages/ml/artifacts/`, load numbers in `infra/loadtest/results.md`.
Where a number comes from synthetic data or simulation, the bullet says so — that
honesty is itself a talking point.

## Project Header

**DosaDash — AI-Native Cloud Kitchen Platform** (Production: dosadash.venkateshs.dev · GitHub: venkathub/dosadash)
*Python, FastAPI, LangGraph, PostgreSQL/pgvector, Next.js, XGBoost, MLflow, Langfuse, Docker — deployed on a 4 GB VPS*

## Bullets (pick 4–6 per application, tailor to JD)

### AI/LLM Engineering
- Built and production-deployed an AI-native cloud-kitchen platform serving a conversational ordering agent (web + Telegram text/voice, EN/Hinglish/Tanglish/Tamil script) with **97.1% order-extraction accuracy, 100% tool correctness and 0 guardrail bypasses** across a 175-conversation live golden set (45 safety-tagged cases), enforced as a **CI merge gate (≥95%)** that blocked two real regressions pre-merge (at 85% and 92.7%).
- Designed a production RAG pipeline (hybrid BM25 + pgvector RRF → LLM rerank → citations) with an event cascade that re-embeds on menu changes; **zero menu hallucinations reach checkout** via DB-grounded validation of every structured `OrderDraft` — enforced by guardrail-bypass evals, not prompt-begging.
- Implemented LangGraph agents with deterministic guardrails — forecast-driven inventory agent (draft POs → human Telegram approval), support agent (refunds structurally impossible to auto-execute; escalation inbox), promo agent, and a text-to-SQL analytics copilot (read-only DB role, SQL allowlist, self-correction) — plus an **MCP server letting Claude Desktop place real orders** through the production order service.
- Shipped a **self-healing SDLC loop**: GUI bug reports + a production sentinel flow through LLM triage (model observes, deterministic policy decides — with an autonomy ladder *earned* by measured verification rates), owner approval via Telegram, and a cloud coding agent that RCAs and ships fixes as PRs through the **same eval-gated merge stack humans face** (independent AI reviewer on a different model, deploy canary with mechanical auto-rollback PRs, read-only prod verifier that labels or reopens). The loop has closed in production: the agent fixed a real React hydration bug and shipped a customer-facing feature.
- Fine-tuned a LoRA aspect-sentiment model (DistilBERT, 16-label multi-hot) that **beat gpt-4o-mini zero-shot on the same held-out set (macro-F1 0.9944 vs 0.9926)** at **₹0 vs ₹3.20 per 1k reviews**, quantized to INT8 ONNX serving at **~57 ms/review on VPS CPU** with 97.2% confident coverage and LLM fallback for the residue (scored nightly via provider Batch API at 50% price).

### LLMOps / Production
- Instrumented the full LLMOps loop: every LLM call traced to Langfuse (session, user, prompt version), token-cost dashboard, Redis **semantic cache with measured hit rate** surfaced in the admin UI, prompt-cache-friendly stable prefixes with the provider's real `cached_tokens` share measured (**48.9% in prod**) — plus litellm routing with a 3-model fallback chain, and per-run cost/cache telemetry for the autonomous fixer.
- Established the eval pipeline as infrastructure: versioned golden datasets with coverage-floor gates (adversarial injection, sold-out, allergen-conflict, per-language floors incl. Tamil ≥0.80), LLM-as-judge tone rubric, key-free asset gates in unit CI, and live suites gating every PR that touches AI paths in GitHub Actions.
- Deployed the 8-service Docker Compose stack (**≈2.6 GB of a 4 GB VPS**) with Caddy, health-checked CI/CD via SSH where **merge-to-main = production deploy**; the pipeline's own smoke test caught a crash-looping deploy that was fixed and hotfixed within minutes (postmortem in repo).
- Hardened for the public internet: tiered rate limiting (LLM endpoints strictest; fail-open on Redis outage; **load-tested shedding 343 abusive requests while served P50 held 17 ms**), PII redaction before any LLM call including batch files, HMAC-verified webhooks.

### ML Engineering
- Trained XGBoost per-dish demand forecasting with festival-calendar features (Pongal/Diwali multipliers) achieving **WAPE 0.421 vs 0.555 naive lag-7 baseline (−24%)** on synthetic history; nightly Celery scoring with MLflow registry where champions are **promoted only on measured improvement**.
- Built an implicit-feedback ALS recommender: **Recall@10 0.387 vs 0.352 popularity baseline, and tail-Recall@10 0.304 vs 0.000** — on a 60-item catalog the long-tail number is the personalization story; embedding cold-start fallback; serving folds live DB history via ALS normal equations (numpy-only, no training deps in the image).
- Ran a 3-arm simulated A/B (3,133 holdout checkout sessions) for checkout combo suggestions: **15.7% attach vs 13.3% random, +4.5% AOV vs control** — with the caveat documented in the artifact that this validates taste recovery on synthetic personas, not real-world uplift.
- Shipped an ETA regression (MAE 3.35 min at the synthetic noise floor — oracle 3.32) powering live order tracking.

### Full-Stack / Platform
- Implemented passwordless OTP auth with pluggable delivery channels (Telegram DM / demo UI) behind an `OtpChannel` interface, rotating refresh tokens, tiered rate limiting, and 4-role RBAC with audit logging.
- Built the complete backoffice (menu ops with instant 86 propagation, inventory + VLM invoice OCR with human review, CRM churn scoring, promos, GST reports, eval scoreboard, cost dashboards) on an **event-driven cascade that keeps the AI layer consistent with business state** — a menu edit re-embeds RAG and flushes caches in the same breath; stock changes deliberately don't (separate channel).
- Designed multi-channel order ingestion (web, Telegram, MCP, HMAC-verified simulated aggregator) converging on one order state machine with WebSocket fan-out to kitchen displays; **load-tested at 100 concurrent users with 0 failures (P50 24 ms / P95 210 ms; checkout P95 370 ms across 91 real end-to-end orders)** on a single-process api matching prod topology.
- **889 tests + 159 eval-asset gates**; conventional commits; phase branches → protected `main` with four required checks; every phase deployed to production before the next began.

## Interview Talking Points

1. **Why one Postgres for OLTP + vectors?** Right-sized trade-off at this scale; HNSW + FTS in one box; migration path to a dedicated vector DB is a config seam, not a rewrite.
2. **Zero-hallucination guardrail** — structured outputs + DB validation beats prompt-begging; show the guardrail-bypass suite (45 adversarial cases, 0 bypasses).
3. **Evals as merge gates** — show the two PRs CI actually blocked (85% and 92.7% runs on the scoreboard) and the fixes (data calibration + prompt v2/v3); also the flaky-first re-run protocol.
4. **LoRA vs API benchmark** — the committed accuracy/cost/latency artifact; when fine-tuning wins (high-volume, narrow-label tasks) and the honesty note (planted-label recovery, not human agreement).
5. **Event cascade** — how AI stays consistent with business state (most demos miss this); why inventory events deliberately DON'T re-embed RAG.
6. **Measure, don't assume** — raw-count ALS lost to popularity until log1p; QUInt8 beat QInt8 empirically; fixed-priority combo ranking lost to random in the A/B sim; cache tuning deferred until metrics existed.
7. **Scaling story** — service boundaries, provider interfaces, pub/sub, `brand_id` already in schema → config change, not rewrite.
8. **Scope discipline** — simulated aggregator, deferred multi-brand UI, explicit cut line; 12 weeks, nothing cut in the end.
9. **The self-healing loop** — why the fixer faces the same merge gates as humans; why autonomy is earned (verification-rate ladder), not granted; the cost postmortem that pinned it to Sonnet; the GitHub-Actions-outage watchdog. Show issue #120 → PR #122: the agent RCA'ing and fixing a documented production bug.
10. **Serving windows vs the LLM** — 15 live-gate iterations proved that *any* serving-hours vocabulary in context made the model hallucinate refusals; the fix was structural (presence = orderability + deterministic serving notes), not prompt engineering.

## Demo Script (3 minutes)

1. (30s) Customer: OTP login (on-screen demo OTP) → voice note in Telegram → agent builds order → pay with test card → live tracking with AI ETA.
2. (45s) Ask "is the ghee roast vegan?" → RAG answer with citations; order in Tamil script; try prompt injection → refusal. Show "my usual".
3. (45s) Owner: 86 an item → agent instantly refuses it (offering allergen-checked alternatives) → approve the AI-drafted PO from Telegram → upload a supplier invoice photo → VLM extraction lands in the review queue.
4. (30s) Backoffice: eval scoreboard (incl. the two blocked runs), Langfuse traces, cost + cache dashboards, analytics copilot refusing a PII query.
5. (30s) Claude Desktop orders a dosa via MCP → it appears on the KDS.

# CLAUDE.md — DosaDash Project Instructions

## Project

DosaDash: AI-native South Indian cloud kitchen platform. Portfolio project for AI Engineer role, production-deployed on AIC Cloud VPS (4 GB RAM). Read `docs/` for the full plan before making architectural decisions. `docs/05-schedule-12-weeks.md` defines the current phase — always work within the current phase's scope.

## Tech Stack (fixed — do not substitute without asking)

- **Backend**: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 async, Alembic, Celery + Redis
- **DB**: PostgreSQL 16 + pgvector (vectors + FTS in same DB — deliberate; no separate vector DB)
- **AI**: LangGraph (agents), litellm (routing: gpt-4o-mini primary → Groq Llama 3.3 70B → Gemini Flash fallback), OpenAI text-embedding-3-small, Groq whisper-large-v3 (STT)
- **Bot**: aiogram (Telegram), webhook mode (not polling) in production
- **Frontend**: Next.js (App Router) + Tailwind — one app, three surfaces: customer `/`, KDS `/kds`, admin `/admin`
- **ML**: XGBoost (forecasting, ETA), implicit ALS (recommender), LoRA fine-tune (sentiment), MLflow registry
- **Ops**: Docker Compose (single VPS), Caddy, GitHub Actions CI/CD via SSH, Langfuse Cloud, promptfoo/pytest evals

## Repository Layout

```
apps/api    → Core FastAPI (auth, menu, orders, payments, admin)
apps/ai     → AI service (agents, RAG, guardrails, MCP server, ML inference)
apps/bot    → aiogram Telegram adapter (thin — NO business logic here)
apps/web    → Next.js (customer + KDS + admin)
packages/ml → datagen, forecasting, recsys, fine-tuning
packages/shared → Pydantic schemas shared across services
evals/      → golden datasets, suites, LLM-as-judge rubrics
knowledge/  → RAG source markdown (recipes, allergens, FAQs, policies)
infra/      → docker-compose.yml, Caddyfile, deploy scripts
```

## Hard Rules

1. **Provider interfaces**: payments (`PaymentProvider`), OTP delivery (`OtpChannel`), LLM calls (litellm only — never call OpenAI SDK directly). Swappability is a core story of this project.
2. **No hallucinated menu items**: every `item_id` the order agent emits MUST be validated against the DB before order placement. This guardrail is non-negotiable.
3. **Structured outputs everywhere**: agent outputs are Pydantic models (`OrderDraft` etc.), never free-text parsing.
4. **Event cascade**: mutations to business state (menu edit, kitchen pause, 86'd item) publish to Redis pub/sub; AI layer subscribes (re-embed RAG, bust caches, agent behavior). Never let the AI layer drift from business state.
5. **Evals are merge gates**: changes to prompts/agents/RAG must pass `evals/` suites in CI. Add eval cases for every new agent capability.
6. **Trace everything**: every LLM call goes through litellm with Langfuse callback (session_id, user_id, prompt version tag).
7. **4 GB RAM budget**: no local LLMs/Whisper on the VPS. All inference via APIs. Check the memory budget in `docs/02-architecture.md` before adding services.
8. **PII**: redact phone numbers before LLM calls and logs.
9. **Secrets**: env vars only (`pydantic-settings`), never committed. Razorpay = TEST keys only.
10. **Telegram bot is an adapter**: it normalizes I/O and renders inline keyboards; all reasoning lives in `apps/ai`.

## Conventions

- Python: ruff (lint+format), pytest, type hints mandatory, async-first
- API: REST under `/api/v1`, WebSockets at `/ws/*`, health at `/healthz`
- Order states: `PLACED → CONFIRMED → COOKING → READY → OUT_FOR_DELIVERY → DELIVERED` (+ `CANCELLED`, `REFUNDED`) — transitions only via `order_service` state machine
- Roles: `customer | kitchen_staff | admin | owner` (JWT claims, RBAC per route)
- Commits: conventional commits (`feat:`, `fix:`, `chore:`, `evals:`)
- Prompts: versioned files in repo (`apps/ai/prompts/`), tagged in Langfuse

## Domain Notes

- Menu: South Indian — dosa varieties, idli/vada, pongal, biryani, Chettinad curries, filter coffee. Veg/vegan/Jain flags, spice levels, allergens matter.
- Synthetic data: festival multipliers (Pongal ×3 idli/pongal, Diwali sweets), weekend biryani spikes — see `packages/ml/datagen`.
- Eval languages: English, Hinglish, Tanglish conversations in golden sets.
- Currency INR, GST on bills (5% food), prices realistic (dosa ₹80–180).

## Git Workflow (docs/08 — mandatory)

- `main` = production, protected. **Never commit directly to main.**
- Each schedule phase = its own `phase/N-*` branch; small `feat/*`/`fix/*` PRs merge into the phase branch; completed phase squash-merges into `main` via a Phase PR using the checklist in docs/08.
- Merge to `main` = production deploy. Rollback = revert PR.
- Conventional commits. PRs touching `apps/ai/**`, prompts, or `evals/**` must update eval cases.

## Current Status

- [x] Planning complete (docs/)
- [x] Phase 0: Foundation — COMPLETE (merged to main #2, deployed via CI)
- [x] Phase 1: Core platform + Auth — COMPLETE (PRs #3–#11: menu APIs, OTP/JWT/RBAC, order state machine + checkout, KDS + WS + event bus, customer web UI, Razorpay TEST, addresses/preferences, Telegram linking + DM OTP + unlink)
- [x] Phase 2: Admin Backend I — COMPLETE (PRs #13–#19 into phase branch: menu ops, settings/pause/staff-RBAC/audit, order mgmt + refunds, combos/ingredients/recipes, nutrition LLM enrichment [first litellm + Langfuse + eval-gate code], hours/schedule enforcement, admin web UI; migrations c41f7a2d9b03 + e7b9c4d15a22; squash-merged to main, deployed via CI)
- [x] Phase 3: RAG + Order Agent — COMPLETE (PRs #21–#28 into phase branch: knowledge base + generated allergen guide, hybrid RAG + citations + re-embed cascade, LangGraph order agent + DB-validated guardrail, SSE streaming + prompt caching, web + Telegram adapters on one graph; migrations a3f8d21c7b90 + b7c4e92f1a05; live evals 18/18 · 12/12 · 15/15 @ 0.95 gate, Langfuse traces verified; squash-merged to main via Phase PR, deployed via CI)
- [ ] Phase 4: Evals + LLMOps — IN PROGRESS on `phase/4-evals-llmops` (fix: meal periods + 52-dish menu folded in; feat branches pushed: golden set 18→80 tagged cases + coverage-floor asset gates, tool_correctness + guardrail_bypass suites + tone LLM-as-judge rubric v1 + one-pass run_live_evals.py, CI live merge gate live-evals.yml @ order_accuracy ≥ 0.95. Remaining: live eval run verification, eval_runs table + admin scoreboard, semantic cache, cost dashboard)
- Update this checklist as phases complete.

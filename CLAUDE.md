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
- [ ] Phase 3: RAG + Order Agent — FEATURE-COMPLETE on `phase/3-rag-order-agent` (Phase PR to main pending live-eval run + deploy)
  - [x] Knowledge base: generated allergen guide (no-drift test), menu guides, FAQ, policies (PR #21)
  - [x] RAG ingestion + pgvector hybrid search (FTS+vector RRF) + `/internal/rag/search` + retrieval evals; migration a3f8d21c7b90 (PR #22)
  - [x] Grounded cited answers `/internal/rag/answer` + menu re-embed cascade + startup knowledge ingest + answer evals (PR #23)
  - [x] LangGraph order agent: `OrderDraft`, DB-validated item guardrail, prefs, 86/pause awareness, `/internal/agent/chat` + order_accuracy golden set (PR #24)
  - [x] SSE token streaming + provider prompt caching (stable [prompt, MENU] prefix) (PR #25)
  - [x] Web chat adapter: `/api/v1/chat[/stream]` proxy + streaming ChatWidget → existing checkout (PR #26)
  - [x] Telegram adapter: draft-edit streaming, inline place/clear, `channel=TELEGRAM` orders (PR #27)
  - [ ] Before Phase PR: run live evals with keys (`retrieval_eval`, `rag_answer_eval`, `order_agent_eval`), verify Langfuse traces, deploy
- Update this checklist as phases complete.

# 02 — Full Architecture

## 1. High-Level System Architecture

```
                                   ┌──────────────────────┐
                                   │   Cloudflare (Free)  │
                                   │  DNS · CDN · WAF ·   │
                                   │  DDoS · Rate-limit   │
                                   └──────────┬───────────┘
                                              │ HTTPS (443)
┌──────────────── CLIENTS ────────────┐       │
│ • Customer Web App (Next.js PWA)    │       │        ┌─────────────────────┐
│ • Kitchen Display System (KDS)      │◄──────┤        │  Telegram Servers   │
│ • Admin/Owner Dashboard             │       │        └─────────┬───────────┘
│ • Telegram App (chat/voice/buttons) │       │                  │ webhook POST
│ • Claude Desktop (via MCP)          │       │                  │
└─────────────────────────────────────┘       ▼                  ▼
╔═════════════════════════ AIC CLOUD VPS (4 GB) ══════════════════════════════╗
║  ┌────────────────────── Caddy Reverse Proxy ───────────────────────────┐   ║
║  │  auto-HTTPS · gzip · routes: / →web · /api →core · /tg →bot · /mcp   │   ║
║  └───────┬──────────────────┬───────────────────┬───────────────────────┘   ║
║          ▼                  ▼                   ▼                           ║
║  ┌──────────────┐   ┌──────────────────┐  ┌──────────────┐                  ║
║  │ Next.js SSR  │   │  Core API        │  │ Telegram Bot │                  ║
║  │ (web + KDS + │   │  (FastAPI)       │  │ (aiogram)    │                  ║
║  │  admin)      │   │  REST + WebSocket│  │ thin adapter │                  ║
║  └──────────────┘   └───┬──────────┬───┘  └──────┬───────┘                  ║
║                         │          ▼             ▼                          ║
║                         │   ┌─────────────────────────────┐                 ║
║                         │   │   AI Service (FastAPI)      │                 ║
║                         │   │ • Order Agent (LangGraph)   │                 ║
║                         │   │ • RAG service               │                 ║
║                         │   │ • Recommender · ETA · QC    │                 ║
║                         │   │ • Guardrails middleware     │                 ║
║                         │   │ • MCP server (SSE)          │                 ║
║                         │   └──────┬──────────────┬───────┘                 ║
║                         ▼          ▼              │                         ║
║  ┌─────────────────────────────────────┐          │                         ║
║  │ PostgreSQL 16 + pgvector            │          │                         ║
║  │ (OLTP + vectors + FTS + jobs)       │          │                         ║
║  └─────────────────────────────────────┘          │                         ║
║  ┌─────────────────────────────────────┐          │                         ║
║  │ Redis (cache · semantic cache ·     │◄─────────┤                         ║
║  │ OTP state · Celery · pub/sub)       │          │                         ║
║  └─────────────────────────────────────┘          │                         ║
║  ┌─────────────────────────────────────┐          │                         ║
║  │ Celery Worker + Beat                │◄─────────┘                         ║
║  └─────────────────────────────────────┘                                    ║
╚══════════════════════════════════╤══════════════════════════════════════════╝
                                   │ outbound HTTPS only
        ┌──────────────┬───────────┼──────────────┬───────────────┐
        ▼              ▼           ▼              ▼               ▼
  ┌───────────┐ ┌───────────┐ ┌──────────┐ ┌───────────┐ ┌──────────────┐
  │ OpenAI    │ │ Groq      │ │ Langfuse │ │ Razorpay  │ │ Telegram API │
  │ gpt-4o-   │ │gpt-oss120b│ │ Cloud    │ │ Test Mode │ │ (send msgs)  │
  │ mini      │ │ + Whisper │ │ (traces) │ │ (sandbox) │ └──────────────┘
  └───────────┘ └───────────┘ └──────────┘ └───────────┘
        └───── routed via litellm (fallback chain + cost tracking) ─────┘
```

**Key principle**: modular monolith — 3 deployable services (core API, AI service, bot) + 1 worker + 1 frontend, sharing one Postgres. Microservice *boundaries* without microservice *overhead* — right-sized for 4 GB RAM.

## 2. Services

### 2.1 Core API (FastAPI)
```
apps/api/
├── routers/   auth · menu · cart · orders · payments · account · inventory ·
│              reviews · admin (menu-ops, promos, crm, reports, settings)
├── services/  order_service (state machine) · payment_service (PaymentProvider
│              ABC → RazorpayTestProvider) · otp_service (OtpChannel ABC →
│              DemoOtpChannel | TelegramOtpChannel) · notification_service
├── ws/        /ws/kds · /ws/track/{order_id}
├── models/    SQLAlchemy 2.0 async + Alembic
└── core/      config · JWT (15m access + 30d rotating refresh, httpOnly) ·
               RBAC (customer/kitchen_staff/admin/owner) · rate limiting
```
- **Order state machine**: `PLACED → CONFIRMED → COOKING → READY → OUT_FOR_DELIVERY → DELIVERED` (+ `CANCELLED`, `REFUNDED`). Transitions → Redis pub/sub → WS fan-out (KDS, tracking) + Telegram push.
- **Payments**: Razorpay Orders API (test keys) → signature-verified webhook → confirm order.
- **OTP auth**: 6-digit, hashed in Redis, TTL 5 min, max 3 attempts, 60s resend cooldown. Demo mode returns OTP in response for on-screen "📱 Demo SMS" banner; Telegram channel DMs it via bot.
- **Telegram linking**: deep link `t.me/DosaDashBot?start=<link_token>` binds `tg_user_id` ↔ phone; Telegram Login Widget on web as one-tap alternative.

### 2.2 AI Service (FastAPI)
```
apps/ai/
├── agents/   order_agent (LangGraph) · inventory_agent · support_agent ·
│             promo_agent · sql copilot · feedback triage
├── rag/      ingestion · chunking · hybrid retrieval · rerank
├── llm/      litellm router · semantic cache · structured-output helpers
├── guardrails/  input (injection/PII) · output (hallucination/policy ·
│             serving-notes · self-correction retry)
├── ml_inference/  eta · recommender · forecast reader · vision QC ·
│             INT8 ONNX sentiment · invoice OCR · image gen · translation
├── speech/   Groq Whisper STT
├── prompts/  versioned prompt files (tagged in Langfuse) — order_agent_v5,
│             feedback_triage_v2, review_sentiment_v1, … (see apps/ai/prompts/)
└── mcp/      MCP server: get_menu · place_order · check_inventory (stdio,
              runs client-side — zero VPS RAM)
```

**Order Agent (LangGraph):**
```
 msg ─► Guardrails (inject/PII) ─fail─► refusal
          │
          ▼
       Intent Router ── menu_qa ──► RAG node ─► grounded answer + citations
          │ order intent
          ▼
       Order Builder (tool loop): search_menu · get_item_details ·
         add_to_cart · apply_customization · check_availability
          ▼
       Validator: Pydantic OrderDraft — every item_id validated vs DB
         ⇒ ZERO hallucinated dishes (hard guardrail)
          ▼
       Confirm gate (web button / Telegram inline keyboard, human-in-loop)
          ─► place_order tool ─► Core API ─► payment link
```
- State (cart, preferences, language) checkpointed in Postgres → survives restarts.
- Same graph serves web chat and Telegram; channel adapters translate I/O only.
- Agent context auto-injects logged-in user's preferences (diet/allergens/spice).
- Respects live business state: 86'd items and kitchen-pause via event subscriptions.
- Serving windows (Phase 11): the agent's menu context lists ORDERABLE dishes only
  (presence = orderability); deterministic `serving_notes` append the window/sold-out
  story to replies, and a one-round self-correction retry catches contradiction
  signatures — the model never sees raw serving-hours vocabulary (every design that
  exposed it caused hallucinated refusals, measured across 15 live-gate runs).
- Long-term memory: per-order episodes → "my usual" derived deterministically and
  injected as volatile state (cache-stable prefix untouched).

**RAG pipeline:**
```
Sources: knowledge/ (recipes, ingredients/allergens, FAQs, policies)
  → semantic chunking (400 tok, 15% overlap, dish metadata)
  → text-embedding-3-small → pgvector (HNSW)
Query: embed ─┐
              ├─► RRF fusion (top-20) → LLM rerank (top-4) → answer + citations
  BM25 (FTS) ─┘
Menu edits publish event → Celery re-embeds affected chunks (no drift).
```

**LLM routing (litellm):**
| Tier | Model | Use |
|---|---|---|
| Primary | gpt-4o-mini | agent reasoning, structured extraction |
| Fast/free | Groq gpt-oss-120b (Llama 3.3 retired 2026-08-16) | intent routing, rerank, casual chat |
| Fallback | Groq → Gemini Flash | outage chain |
| STT | Groq whisper-large-v3 | Telegram voice notes |

Semantic cache: Redis, cosine ≥ 0.95 on cached Q&A (~40% cost cut). All calls traced to Langfuse (tokens/cost/latency/session). Daily budget kill-switch.

### 2.3 Telegram Bot (aiogram) — thin adapter
```
webhook → normalize (text | voice→STT | callback) → AI service /chat
        → render text + inline keyboards (menu cards, cart ±, confirm, pay link)
Push: order status / OTP / PO approvals via Redis pub/sub → bot send
Commands: /start (link account) · /menu · /myorders · voice note = order
```

### 2.4 Celery Worker + Beat (as-built schedule, IST)
| Schedule | Job |
|---|---|
| nightly 02:00 | per-dish demand forecast (14-day recursive) → `forecasts` |
| nightly 02:30 | inventory agent: stock vs forecast → draft PO → owner approval (open-PO dedup) |
| nightly 03:00 | CRM segments: RFM + personal-rhythm churn + LTV |
| nightly 03:30 | review scoring: local INT8 first, unconfident residue → OpenAI Batch API |
| hourly @ :20 | review batch poll (ingest completed Batch API jobs) |
| every 15 min | feedback triage (LLM assessment → deterministic decide()) |
| every 15 min (offset :05) | GitHub reconciler: label/PR truth-sync for the feedback loop |
| every 5 min (offset :02) | fixer dispatch watchdog (stall detect + auto-resume after Actions outages) |
| every 5 min (offset :04) | sentinel scan: healthz fleet probes, 5xx bursts, consecutive eval-gate reds |
| hourly | worker heartbeat |
| weekly Sun 04:30 | janitor: computed flaky-eval list, translation backlog, stale approvals |
| weekly (local/CI) | recommender + forecast retrain → MLflow (promote via `champion` alias) |
| on-event | RAG re-ingestion · notifications · re-embed cascade |

### 2.5 Frontend (Next.js — one app, six surfaces, "Madras Pop" design system)
- `/` customer: menu (60 dishes, serving-window annotation + ⏰ badges, EN/தமிழ் toggle, ✨ AI photos, high-protein filter), semantic search, cart, Dosa-Genie chat widget, OTP login, account (history, reorder, addresses, preferences, loyalty), live tracking with AI ETA, recommendations + combo upsell, 🐞 feedback FAB
- `/orders` history + reviews (★ rate delivered orders) + 🛟 support chat
- `/kds` kitchen: live WS queue with channel badges + elapsed timers, bump buttons, 📷 dish-photo QC
- `/admin` owner: Dashboard · Orders · Menu ops · Inventory+PO approvals+invoices · Promos+Coupons · CRM · Reviews inbox (AI-drafted replies) · Reports (GST CSV) · Translations · Images · Copilot · Evals scoreboard · Costs+cache panel · Feedback · Settings — grouped Operations / Growth / AI Studio / System
- `/demo` demo guide: credentials, Razorpay test cards, feature tour
- `/fixer` self-healing portal: 6-lane pipeline board with inline approve/reject, funnel metrics (MTTR, verification rate, agent spend), live WS feed, outage/stall banners

## 3. Event Cascade (signature architecture move)

Redis pub/sub keeps AI layer consistent with business state:
```
menu.updated      → re-embed RAG chunks + bust bot/menu caches + semantic-cache flush
menu.translation  → translation overlay refresh (approved Tamil names → agent aliases)
menu.image        → menu photo publish/unpublish
item.86d          → agent's check_availability excludes instantly
kitchen.paused    → agent refuses new orders gracefully (web + Telegram)
order.status.*    → WS fan-out + Telegram push
po.drafted        → owner Telegram approval message
pubsub:inventory  → stock changes (deliberately OFF pubsub:menu — stock moves
                    never re-embed RAG)
pubsub:feedback   → self-healing-loop lifecycle events → /fixer portal WS +
                    Telegram anchor cards (own channel — never touches RAG/KDS)
```

## 3b. Self-Healing SDLC Loop (Phases 13–15)

Production maintains itself through an eval-gated autonomous loop; full design
records live in `docs/14` and `docs/15`.

```
🐞 GUI reports (customer/staff/anon)          sentinel beat (every 5 min):
        │                                     healthz fleet · 5xx bursts ·
        ▼                                     ≥2 consecutive eval-gate reds
feedback_reports (PII-redacted, deduped) ◄────┘
        │  GitHub issue mirror (labels, UNTRUSTED-fenced body)
        ▼
LLM triage (feedback_triage_v2) — model OBSERVES, deterministic decide() DECIDES:
  AUTO_FIX iff BUG ∧ effort S ∧ risk LOW ∧ actionable (SYSTEM reports NEVER auto-fix)
  autonomy is an EARNED ladder: AUTO_FIX_M unlocks only at ≥20 merged fixes with
  ≥0.90 verification rate; HUMAN_ONLY zones (migrations/workflows/secrets) are structural
        │
        ▼
owner approval via Telegram card (+ AI-drafted ## Spec comment for features/M+)
        │  ai:approved / ai:auto-fix label
        ▼
Claude fixer (claude-code-action, pinned Sonnet, 60-turn budget, tool allowlist,
kill switch): RCA comment → fix on fix/issue-N → PR through the FULL merge-gate
stack (ruff+pytest · web build · eval suites · LIVE eval gate · UI smoke ·
independent Haiku AI-review verdict — required checks on main)
        │  auto-merge (S/LOW) or human merge
        ▼
deploy + deterministic canary (20×30s public probes; breach → mechanical
git-revert PR via auto-merge, oscillation-guarded)
        │
        ▼
Haiku prod verifier (read-only tools) probes the fix live → ai:verified or REOPENS
        │
        ▼
observability: feedback_events timeline · fixer_runs (cost + cached-token
telemetry) · Telegram lifecycle anchors · /fixer portal · GitHub webhook (HMAC)
+ 15-min reconciler so a missed delivery is never permanent drift · dispatch
watchdog (survives GitHub Actions outages, 3-retry cap)
```

The loop has closed for real: the fixer RCA'd and shipped a React hydration
bug fix and a customer-facing feature (high-protein filter), both through the
live eval gate, human-approved via Telegram, deployed and verified in prod.

## 4. Deployment (4 GB memory budget)

```
┌─────────────────────────────┬────────────┐
│ postgres:16 + pgvector      │ ~450 MB    │
│ redis:7 (maxmemory 256mb)   │ ~150 MB    │
│ core-api (uvicorn ×2)       │ ~350 MB    │
│ ai-service (uvicorn ×1)     │ ~650 MB    │ incl. INT8 DistilBERT (~150 MB)
│ bot (aiogram)               │ ~160 MB    │ Phase 9: lockfile-refresh rebuild grew RSS (measured 151 MiB)
│ celery worker+beat          │ ~300 MB    │
│ next.js (standalone)        │ ~300 MB    │
│ caddy                       │ ~40 MB     │
├─────────────────────────────┼────────────┤
│ total                       │ ~2.35 GB   │ + OS ~400 MB ⇒ ~1.25 GB headroom
└─────────────────────────────┴────────────┘
+ 2 GB swapfile. No local LLM/Whisper inference on VPS — generative AI via
APIs only (Hard Rule 7). Tiny classical/tuned models DO serve locally by
design: xgboost (forecast/ETA), ALS item factors (recsys), and the INT8
ONNX DistilBERT sentiment champion (Phase 8 — the whole point is ₹0 CPU
serving on this box).
MLflow runs locally/CI; artifacts synced to VPS.
```

**CI/CD**: push → GitHub Actions (ruff/pytest → **eval gate** → docker build to GHCR) → SSH → `docker compose pull && up -d` (health-checked) → smoke `/healthz` → Telegram "✅ deployed <sha>". Nightly `pg_dump` backup (7-day retention). UptimeRobot on `/healthz`.

**Security**: Cloudflare-proxied DNS (VPS IP hidden) · UFW 80/443/SSH-key only · JWT+RBAC · Telegram webhook secret · Razorpay signature verification · injection filters · PII redaction pre-LLM · per-user/IP rate limits · litellm daily spend kill-switch · secrets in env only · staff audit log.

## 5. Scaling Story (interview answer)

1 kitchen → N: extract AI service to own node; Postgres → managed + read replicas; Redis cluster; dedicated WS gateway; `brand_id` partitioning (schema-ready day 1). The seams (service boundaries, provider interfaces, pub/sub) make this config, not rewrite.

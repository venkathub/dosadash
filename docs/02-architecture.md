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
├── agents/   order_agent (LangGraph) · inventory_agent · support_agent
├── rag/      ingestion · chunking · hybrid retrieval · rerank
├── llm/      litellm router · semantic cache · structured-output helpers
├── guardrails/  input (injection/PII) · output (hallucination/policy)
├── ml_inference/  eta · recommender · forecast reader · vision QC
├── speech/   Groq Whisper STT · edge-tts TTS
├── prompts/  versioned prompt files (tagged in Langfuse)
└── mcp/      MCP server: get_menu · place_order · check_inventory
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

### 2.4 Celery Worker + Beat
| Schedule | Job |
|---|---|
| nightly 02:00 | per-dish demand forecast (14-day) → `forecasts` |
| nightly 02:30 | inventory agent: stock vs forecast → draft PO → owner approval |
| nightly 03:00 | CRM segments: RFM + churn-risk scoring |
| weekly | recommender + forecast retrain → MLflow (promote via `champion` alias) |
| on-event | review sentiment scoring · RAG re-ingestion · notifications |

### 2.5 Frontend (Next.js — one app, three surfaces)
- `/` customer: menu, semantic search, cart, chat widget, OTP login, account (history, reorder, addresses, preferences, loyalty), live tracking with AI ETA, recommendations + combo upsell
- `/kds` kitchen: live WS queue, AI priority + predicted prep time, bump buttons
- `/admin` owner: Dashboard (revenue/AOV/anomaly flags) · Orders · Menu ops · Inventory+PO approvals · Promos · CRM · Reviews inbox (AI-drafted replies) · Reports (GST CSV) · Settings (hours/zones/pause/staff) · Eval scoreboard

## 3. Event Cascade (signature architecture move)

Redis pub/sub keeps AI layer consistent with business state:
```
menu.updated      → re-embed RAG chunks + bust bot/menu caches
item.86d          → agent's check_availability excludes instantly
kitchen.paused    → agent refuses new orders gracefully (web + Telegram)
order.status.*    → WS fan-out + Telegram push
po.drafted        → owner Telegram approval message
```

## 4. Deployment (4 GB memory budget)

```
┌─────────────────────────────┬────────────┐
│ postgres:16 + pgvector      │ ~450 MB    │
│ redis:7 (maxmemory 256mb)   │ ~150 MB    │
│ core-api (uvicorn ×2)       │ ~350 MB    │
│ ai-service (uvicorn ×1)     │ ~500 MB    │
│ bot (aiogram)               │ ~120 MB    │
│ celery worker+beat          │ ~300 MB    │
│ next.js (standalone)        │ ~300 MB    │
│ caddy                       │ ~40 MB     │
├─────────────────────────────┼────────────┤
│ total                       │ ~2.2 GB    │ + OS ~400 MB ⇒ ~1.4 GB headroom
└─────────────────────────────┴────────────┘
+ 2 GB swapfile. No local model inference on VPS — APIs only.
MLflow runs locally/CI; artifacts synced to VPS.
```

**CI/CD**: push → GitHub Actions (ruff/pytest → **eval gate** → docker build to GHCR) → SSH → `docker compose pull && up -d` (health-checked) → smoke `/healthz` → Telegram "✅ deployed <sha>". Nightly `pg_dump` backup (7-day retention). UptimeRobot on `/healthz`.

**Security**: Cloudflare-proxied DNS (VPS IP hidden) · UFW 80/443/SSH-key only · JWT+RBAC · Telegram webhook secret · Razorpay signature verification · injection filters · PII redaction pre-LLM · per-user/IP rate limits · litellm daily spend kill-switch · secrets in env only · staff audit log.

## 5. Scaling Story (interview answer)

1 kitchen → N: extract AI service to own node; Postgres → managed + read replicas; Redis cluster; dedicated WS gateway; `brand_id` partitioning (schema-ready day 1). The seams (service boundaries, provider interfaces, pub/sub) make this config, not rewrite.

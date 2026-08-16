# DosaDash — AI-Native South Indian Cloud Kitchen Platform

> Portfolio project for AI Engineer role. End-to-end cloud kitchen platform where **every subsystem is AI-driven**, production-deployed on AIC Cloud VPS.

## What This Is

A full cloud-kitchen business platform (customer app + Telegram bot + kitchen display + owner back office) that demonstrates **all major AI engineering concepts** in one cohesive product:

LLM apps · Structured outputs · Production RAG · Agentic AI (LangGraph) · MCP · Fine-tuning (LoRA) · Embeddings/vector search · Classical ML (XGBoost forecasting) · Recommenders · Speech (Whisper) · Computer vision · LLM evals in CI · Guardrails · LLMOps (Langfuse)

## Locked Decisions

| Decision | Choice |
|---|---|
| Messaging channel | Telegram bot (aiogram, webhooks) — NOT WhatsApp |
| Payments | Razorpay **Test Mode** (no KYC/business account needed) behind `PaymentProvider` interface |
| Auth | Passwordless OTP — shown in demo UI **or** delivered via Telegram DM (`OtpChannel` interface) |
| Hosting | AIC Cloud VPS, **4 GB RAM**, Docker Compose, Caddy, Cloudflare free |
| LLMs | gpt-4o-mini primary (~$10–20/mo) + Groq Llama 3.3 70B & Whisper free tier, routed via litellm |
| Observability | Langfuse Cloud free tier |
| Timeline | 12 weeks @ 10–15 hrs/week |

## Documentation Map

| File | Contents |
|---|---|
| [docs/01-plan-overview.md](docs/01-plan-overview.md) | Deep analysis, goals, risks, cost budget |
| [docs/02-architecture.md](docs/02-architecture.md) | Full system architecture, services, diagrams, deployment |
| [docs/03-feature-ai-matrix.md](docs/03-feature-ai-matrix.md) | Feature ↔ AI concept mapping (the resume core) |
| [docs/04-business-usecases.md](docs/04-business-usecases.md) | Customer + business-owner use cases, auth flows, admin modules |
| [docs/05-schedule-12-weeks.md](docs/05-schedule-12-weeks.md) | Phase-by-phase build schedule with deliverables |
| [docs/06-schema.md](docs/06-schema.md) | Database schema (Postgres + pgvector + Redis keyspaces) |
| [docs/07-resume-bullets.md](docs/07-resume-bullets.md) | Target resume bullets (replace numbers with real metrics) |
| [docs/08-git-workflow.md](docs/08-git-workflow.md) | Branching strategy: phase branches → protected `main` (prod), PR gates |
| [CLAUDE.md](CLAUDE.md) | Claude Code project instructions for building this |

## Golden Rules

1. **Deploy from day 1** — live URL before any features.
2. **Evals before more AI** — Phase 3 (eval gates in CI) comes before voice/vision/recsys.
3. **End of Week 6 = resume-worthy milestone** — deployed, eval-gated conversational ordering with RAG. Everything after compounds.
4. **Cut line if slipping**: mock-aggregator → multi-brand UI → vision QC → image gen → extra localization. Never cut evals.
5. **Never commit to `main`** — phase branches → PR with CI+eval gates → squash-merge to prod (docs/08).

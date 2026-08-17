# 01 — Plan Overview & Deep Analysis

## Goal

Build and production-deploy one cohesive AI-native product (South Indian cloud kitchen) that demonstrates every AI engineering concept hiring managers scan for in 2026 — production RAG, agents, evals, structured outputs, fine-tuning, classical ML, LLMOps — with a live URL, real metrics, and a defensible architecture.

## Research Findings (Aug 2026)

**Cloud kitchen domain requirements** (Petpooja/StackFood/Zonal-class systems): order management + aggregator integration, Kitchen Display System (KDS), real-time inventory, menu management, delivery logistics, CRM/analytics, POS/payments, multi-brand support.

**Where AI is used in production in this industry**: demand forecasting, dynamic pricing, inventory/waste optimization, chatbot + voice ordering, review sentiment analysis, kitchen order prioritization, personalized recommendations.

**What AI Engineer portfolios need in 2026**: production RAG (not notebooks), agentic workflows, eval pipelines in CI, structured outputs, fine-tuning comparisons, LLMOps observability, deployed URL. One cohesive product > five disconnected demos.

**Key insight**: Don't build "a food app with a chatbot bolted on." Build a food platform where every subsystem is AI-driven, each mapping to a named AI concept defensible in interviews.

## Locked Decisions

| Area | Decision | Rationale |
|---|---|---|
| Channel | Telegram (aiogram) | Free Bot API, no business account (unlike WhatsApp), inline keyboards, built-in voice notes → zero extra client work for voice ordering |
| Payments | Razorpay Test Mode | Full sandbox without KYC/business account. Test cards on demo page. `PaymentProvider` interface → Stripe/Cashfree swappable |
| Auth | OTP (demo-UI or Telegram DM) | No SMS cost/DLT registration. `OtpChannel` interface → MSG91 later is one class |
| Hosting | AIC Cloud VPS 4 GB (~₹400–600/mo) | Cheapest plan that fits full stack with API-based LLMs; UPI billing |
| LLM budget | ~$10–20/mo | gpt-4o-mini primary, Groq free tier (gpt-oss-120b + Whisper; Llama 3.3 retired 2026-08-16) as fast/fallback layer via litellm |
| Observability | Langfuse Cloud free tier | Saves ~1.5 GB RAM vs self-hosting |
| Timeline | 12 weeks @ 10–15 hrs/week | Milestone: Week 6 = deployed, eval-gated ordering agent (already resume-worthy) |

## Monthly Cost Budget

| Item | Cost |
|---|---|
| AIC Cloud 4 GB VPS | ~₹400–600 (~$5–7) |
| Domain | ~₹70/mo amortized |
| LLM APIs | $10–20 dev → ~$3–5 steady state (semantic caching + Groq routing) |
| Langfuse Cloud, Cloudflare, Groq, Razorpay Test, Telegram | $0 |
| **Total** | **~$15–25/mo dev → ~$10/mo steady state** |

## Risks & Mitigations

1. **Scope trap**: Build Phases 0–3 fully before voice/vision. A deployed bot with evals beats 15 half-features. Cut line if slipping: mock-aggregator → multi-brand UI → vision QC. Never cut evals.
2. **"GPT wrapper" perception**: Evals, guardrails, classical ML, and the LoRA-vs-zero-shot comparison elevate it. Lead with those in the README/demo.
3. **Cost blowout**: Groq/caching for dev, semantic cache in prod, litellm daily budget kill-switch, rate-limited public demo.
4. **No real data**: The synthetic data generator (festival/seasonality logic) is itself a talking point — document it.
5. **4 GB RAM**: Memory budget table in architecture doc; 2 GB swap; no local model inference on VPS.

## Success Criteria

- Live URL with custom domain + HTTPS + uptime monitoring
- Customer can: sign up via OTP, order via web chat / Telegram text / Telegram voice, pay (test mode), track live
- Owner can: manage menu, approve AI-drafted POs, view forecasts/CRM/reports, respond to reviews with AI drafts
- CI blocks merges when eval scores regress; Langfuse dashboards show cost/latency/quality
- README with architecture diagram, metrics table, 3-min demo video, blog post

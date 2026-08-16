# 05 — 12-Week Build Schedule (v3, final)

Cadence: 10–15 hrs/week. **Milestone rule: end of Week 6 = deployed, eval-gated conversational ordering — already resume-worthy.** Cut line if slipping: mock-aggregator → multi-brand UI → vision QC. Never cut evals.

| Weeks | Phase | Deliverables |
|---|---|---|
| **1** | Phase 0 — Foundation | Monorepo (`apps/api·ai·bot·web`, `packages/`, `evals/`, `knowledge/`, `infra/`) · Docker Compose (Postgres+pgvector, Redis, Caddy) · GitHub Actions CI/CD via SSH → **live URL on AIC Cloud day 1** · schema v2 (auth/CRM/promos/`brand_id`) · `OtpChannel` + `PaymentProvider` interfaces stubbed · synthetic data generator (12 mo orders, Pongal/Diwali multipliers, weekend biryani spikes, ~500 users with taste personas) · ~40-item South Indian menu seed with ingredients/allergens · Telegram bot registered, webhook echo working in prod |
| **2–3** | Core platform + Auth & Accounts | Menu/cart/checkout APIs · order state machine · KDS with WebSockets · Razorpay test checkout + signature webhook · **OTP signup (demo-UI banner + Telegram DM channel)** · Telegram account linking (deep link + Login Widget) · order history + 1-tap reorder · live tracking WS · addresses · preferences |
| **4** | Admin Backend I | Menu ops (CRUD, 86 toggle, scheduling, customizations, combo builder, recipe/ingredient mapping) · admin order management (modify/cancel/refund) · settings (hours, delivery pincodes, kitchen pause) · staff RBAC · audit log · event cascade bus (Redis pub/sub) |
| **5–6** | RAG + Order Agent | `knowledge/` ingestion → pgvector hybrid RAG (BM25+vector RRF → LLM rerank → citations) · LangGraph order agent with `OrderDraft` structured output · **DB-validated item guardrail (zero hallucinated dishes)** · web chat + Telegram adapters on same graph · agent reads user preferences · respects 86/pause events · menu-edit → re-embed cascade · injection/PII guardrails · litellm routing + Langfuse tracing live |
| **7** | Evals + LLMOps | 150+ golden conversations (EN/Hinglish/Tanglish, typos, adversarial, sold-out ordering, kitchen-paused, allergen conflicts) · suites: order_accuracy, RAG faithfulness, tool correctness, guardrail bypass, tone · LLM-as-judge rubrics · **CI merge gate (fail if order_accuracy < 0.95)** · `eval_runs` table + admin scoreboard · semantic cache · cost dashboard |
| **8** | Classical ML + Admin Backend II | XGBoost per-dish demand forecast (lag-7/14, dow, festival calendar, weather) · ETA regression · MLflow registry + `champion` alias · nightly Celery scoring · Reports (sales, dish P&L, GST CSV) · CRM segments (RFM, churn-risk) · forecast-vs-actual + anomaly flags on dashboard |
| **9** | Agents + MCP | Inventory agent (stock vs forecast → draft PO → owner approval in UI/Telegram → execute) · wastage log · support agent (refund/status, policy guardrails, escalation inbox) · MCP server (`get_menu`, `place_order`, `check_inventory`) — **demo: Claude Desktop orders a dosa** |
| **10** | Voice, Vision, Recsys, Promos | Telegram voice-note ordering (Groq Whisper, EN + 1 Indian language) · dish-photo QC endpoint · implicit ALS recommender + embedding cold-start fallback · checkout combo suggester (measure uplift in synthetic A/B) · coupon engine · AI combo/discount suggestions into admin approval flow · mock-aggregator channel |
| **11** | Fine-tune + Review inbox | LoRA aspect-sentiment model (DistilBERT/Llama-3.2-1B) · benchmark vs gpt-4o-mini zero-shot (accuracy vs ₹/1k reviews) in eval report · reviews inbox with auto-tags + trend alerts + AI-drafted owner replies |
| **12** | Hardening + Story | Rate limiting · semantic cache tuning · locust load test · README with architecture diagram + metrics table · 3-min demo video (customer journey AND owner journey) · blog post · resume bullets with real numbers · demo credentials + test card numbers on demo page |

## Definition of Done (per phase)

- Deployed to production (not just local)
- Tests pass; eval suites updated for any agent/prompt change
- Langfuse traces verified for new LLM paths
- `CLAUDE.md` status checklist updated

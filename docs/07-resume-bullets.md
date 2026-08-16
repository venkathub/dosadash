# 07 — Resume Bullets (targets — replace with YOUR real measured numbers)

## Project Header

**DosaDash — AI-Native Cloud Kitchen Platform** (Production: <your-domain> · GitHub: <repo>)
*Python, FastAPI, LangGraph, PostgreSQL/pgvector, Next.js, XGBoost, MLflow, Langfuse, Docker — deployed on VPS*

## Bullets (pick 4–6 per application, tailor to JD)

### AI/LLM Engineering
- Built and production-deployed an AI-native cloud kitchen platform serving a conversational ordering agent (web + Telegram + voice) with **97% order-extraction accuracy** across 150+ golden multilingual test conversations, enforced as a CI merge gate.
- Designed a production RAG pipeline (hybrid BM25 + pgvector retrieval, RRF fusion, LLM reranking) over recipe/allergen knowledge base, achieving **0 menu hallucinations** via DB-grounded output validation guardrails.
- Implemented LangGraph multi-agent system — inventory procurement agent (forecast-driven PO drafting with human-in-the-loop Telegram approvals) and guardrailed customer-support agent — plus an **MCP server** enabling external LLM clients to place orders via tools.
- Fine-tuned a LoRA aspect-sentiment model matching gpt-4o-mini zero-shot accuracy at **~15x lower inference cost**, with a documented build-vs-API benchmark.

### LLMOps / Production
- Instrumented full LLMOps stack (Langfuse tracing, token-cost dashboards, prompt A/B testing); cut LLM cost per order **~40%** via model routing (Groq/OpenAI fallback chains) and Redis semantic caching.
- Established LLM eval pipeline (golden datasets incl. Hinglish/Tanglish + adversarial injection cases, LLM-as-judge rubrics) gating every PR in GitHub Actions.
- Deployed 7-service Docker Compose stack on a 4 GB VPS with Caddy/Cloudflare, health-checked zero-downtime CI/CD, nightly backups, and uptime monitoring — **~$10/month total running cost**.

### ML Engineering
- Trained XGBoost demand-forecasting models with festival-calendar features (Pongal/Diwali) reducing simulated food waste **~22%**; automated nightly scoring and weekly retraining via Celery + MLflow registry with alias-based rollback.
- Built implicit-feedback (ALS) recommender with embedding-based cold-start fallback; checkout combo suggestions lifted simulated AOV **~12%** in A/B test.
- Shipped prep-time/ETA regression powering live customer order tracking and kitchen queue prioritization.

### Full-Stack / Platform
- Implemented passwordless OTP authentication with pluggable delivery channels (Telegram DM / demo UI), rotating refresh tokens, rate limiting, and 4-role RBAC.
- Built complete cloud-kitchen back office (menu ops with instant "86" propagation, inventory, CRM with churn scoring, promotions, GST reports) using an **event-driven cascade keeping the AI layer consistent with business state** (menu edits auto-re-embed RAG; kitchen-pause propagates to agents in real time).
- Designed multi-channel order ingestion (web, Telegram, simulated aggregator webhooks) behind a unified order state machine with WebSocket fan-out to kitchen displays.

## Interview Talking Points

1. **Why one Postgres for OLTP + vectors?** Right-sized trade-off at this scale; HNSW + FTS in one box; describe the migration path to a dedicated vector DB.
2. **Zero-hallucination guardrail** — structured outputs + DB validation beats prompt-begging.
3. **Evals as merge gates** — show a PR that CI blocked, and the fix.
4. **LoRA vs API benchmark** — accuracy/cost/latency table; when fine-tuning wins.
5. **Event cascade** — how AI stays consistent with business state (most demos miss this).
6. **Scaling story** — seams already in place (service boundaries, provider interfaces, pub/sub, brand_id) → config change, not rewrite.
7. **Scope discipline** — simulated aggregator, deferred multi-brand UI, explicit cuts.

## Demo Script (3 minutes)

1. (30s) Customer: OTP login → voice note in Telegram → agent builds order → pay with test card → live tracking with AI ETA.
2. (45s) Ask bot "is sambar vegan?" → RAG answer with citations; try prompt injection → refusal.
3. (45s) Owner: dashboard anomaly flag → 86 an item → show bot instantly refusing it → approve AI-drafted PO from Telegram.
4. (30s) Langfuse traces + cost dashboard + CI eval gate screenshot.
5. (30s) Claude Desktop orders a dosa via MCP.

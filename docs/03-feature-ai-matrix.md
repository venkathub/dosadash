# 03 — Feature ↔ AI Concept Matrix (Resume Core)

Every product feature maps to a named AI concept you can defend in interviews.

> **As-built note (2026-08-27): the product features below were recorded as shipped; this does not establish every originally named technique.** Where
> the plan offered options, the shipped choice is noted: #9 fine-tune =
> DistilBERT + LoRA → INT8 ONNX; #10 vision QC = VLM (not YOLO/CLIP); #28
> localization = Tamil live end-to-end (Telugu/Kannada/Hindi = registry
> entries by design). Post-schedule phases added rows 29–33 below.

| # | Product Feature | AI Concept Demonstrated |
|---|---|---|
| 1 | Conversational ordering ("2 masala dosa, less spicy, no onion") — web chat + Telegram | LLM app, **structured outputs / function calling** (Pydantic `OrderDraft` schema) |
| 2 | Menu Q&A: "Is sambar vegan? Allergens in Chettinad curry?" | **Production RAG** (hybrid PostgreSQL FTS + vector, RRF fusion, answer-time source selection, citations, faithfulness guardrail) |
| 3 | Voice ordering via Telegram voice notes (EN + 1 Indian language) | **Speech**: Groq Whisper STT, multilingual handling |
| 4 | Personalized menu & combo recommendations | **Recommender system** (implicit ALS + embedding fallback for cold start) |
| 5 | Daily demand forecast per dish → prep quantities | **Classical ML / time-series** (XGBoost: lag features, festival calendar — Pongal/Diwali, weekends, weather) |
| 6 | Dynamic off-peak discount suggestions | **Candidate mining + constrained generation** (co-order pairs and slow-day offers; no learned bandit policy) |
| 7 | Inventory agent: monitors stock, consumes forecast, drafts POs, Telegram/UI approval | **Agentic AI** (deterministic needs calculation + structured LLM proposal, human-in-the-loop) |
| 8 | Kitchen order prioritization + ETA prediction | **Regression model** (queue depth, prep complexity) + queue optimization |
| 9 | Review analysis: dish-level aspect sentiment, trend alerts, AI-drafted owner replies | **NLP fine-tuning** (LoRA on DistilBERT/Llama-3.2-1B) benchmarked vs gpt-4o-mini zero-shot (accuracy vs cost) |
| 10 | Dish-photo QC (plating completeness/presentation) | **Computer vision** (YOLO/CLIP or VLM endpoint) |
| 11 | Support agent: refunds/order status with policy guardrails | **Bounded support workflow + guardrails** (injection defense, PII redaction, escalation to owner inbox) |
| 12 | Semantic dish search ("something light and tangy") | **Vector search / embeddings** (pgvector exact cosine search) |
| 13 | Eval suite: order accuracy, hallucinated items, RAG faithfulness, guardrail bypass | **LLM evals** (150+ golden conversations incl. Hinglish/Tanglish, LLM-as-judge, CI merge gates) |
| 14 | Tracing, token-cost dashboards, offline prompt/model comparisons | **LLMOps / observability** (Langfuse Cloud, litellm cost tracking, semantic cache) |
| 15 | MCP server: `get_menu`, `place_order`, `check_inventory` — Claude Desktop orders a dosa | **MCP / tool ecosystems** |
| 16 | Anomaly flag on owner dashboard ("sales 30% below forecast") | **Anomaly detection** (forecast residuals) |
| 17 | Churn-risk & RFM customer segmentation | **Rule-based customer analytics** (nightly quintile RFM and churn-decay scoring) |
| 18 | User preferences auto-injected into agent context (diet/allergen/spice) | **Context engineering / personalization** |
| 19 | Token-streamed chat responses (web SSE + Telegram draft-editing) | **Streaming LLM responses** (UX + perceived latency) |
| 20 | "My usual" — agent remembers preferences/orders across sessions ("less spicy like last time") | **Long-term agent memory** (episodic memory store, distinct from session checkpointing) |
| 21 | Owner analytics copilot: "top 5 dishes by margin last weekend?" → chart | **Text-to-SQL agent** (schema-aware, read-only role, SQL validation guardrail, self-correction loop) |
| 22 | Supplier invoice photo upload → line items extracted → auto-matched to PO → stock updated | **Document AI / OCR** (VLM structured extraction, confidence thresholds, human review queue) |
| 23 | Menu item photo generation for new dishes (owner approves) | **Image generation** (gen-AI images clearly labeled; prompt templates per cuisine style) |
| 24 | Nightly bulk jobs (review scoring, embeddings refresh) via provider Batch APIs at 50% cost | **Batch / async inference** |
| 25 | Fine-tuned sentiment model quantized (INT8/GGUF) to serve on 4 GB CPU VPS | **Quantization / efficient inference** |
| 26 | System-prompt + menu-context reuse via provider prompt caching | **Prompt caching** (distinct from semantic caching — both implemented, both explained) |
| 27 | Auto-computed nutrition/calorie estimates per dish from recipe mapping | **LLM structured enrichment** (batch, human-verified) |
| 28 | Menu localization: Tamil/Telugu/Kannada/Hindi menu + bot replies | **Multilingual generation/translation** (eval'd per language) |

## Post-Schedule Additions (Phases 11–15)

| # | Product Feature | AI Concept Demonstrated |
|---|---|---|
| 29 | Per-dish serving windows the agent explains without hallucinating ("Dosa is not available at lunch") | **Context engineering under adversarial measurement** — presence-=-orderability menu payloads, deterministic serving notes, one-round self-correction (15 live-gate design iterations documented) |
| 30 | 🐞 GUI + sentinel bug reports → LLM triage → Telegram approval → cloud coding agent ships the fix as a PR | **Agentic SDLC / self-healing loop** (claude-code-action fixer, RCA-before-code, tool allowlists, kill switches, HUMAN_ONLY zones) |
| 31 | Fixer PRs face the same merge gates as humans; an independent AI reviewer (different model) posts a required verdict; a read-only verifier probes the fix in prod | **Multi-model checks and balances** (no self-review, fail-closed verdicts, verify-or-reopen) |
| 32 | Deploy canary with mechanical auto-rollback PRs; autonomy unlocked only by measured verification rate | **Earned autonomy / progressive delivery** (deterministic $0 canary, oscillation guard, capability ladder) |
| 33 | Per-run agent cost + cached-token telemetry; computed flaky-eval lists from run history | **AgentOps** (spend observability, janitor-computed eval hygiene) |

## Stretch (documented as "considered — deferred", itself an interview signal)

| Concept | Why deferred |
|---|---|
| GraphRAG (ingredient/recipe knowledge graph) | Relational recipe mapping already answers allergen paths; add only if RAG evals show multi-hop failures |
| A2A (agent-to-agent protocol) | MCP covers the interop story; A2A adds no user value at single-kitchen scale |
| DPO/RLHF preference tuning | LoRA SFT + eval comparison tells the fine-tuning story; DPO needs preference data the project can't generate honestly |
| Edge/on-device inference | No offline requirement in a cloud kitchen |

## Coverage Checklist

- [x] LLM applications & prompt engineering
- [x] Structured outputs / function calling
- [x] RAG (hybrid retrieval, source selection, citations; owner-reviewed faithfulness calibration complete)
- [x] Embeddings & vector search
- [x] Agents (LangGraph ordering, guarded actions, HITL; relational inventory recovery; order graph checkpointing deferred)
- [x] Multi-stage SDLC coordination (fixer/reviewer/verifier; no general business-agent delegation)
- [x] MCP
- [x] Guardrails & AI security (injection, PII, policy)
- [x] Evals (golden sets, LLM-as-judge, CI gates)
- [x] LLMOps (tracing, cost, semantic caching, model routing/fallback; online A/B deferred)
- [x] Fine-tuning (LoRA) + build-vs-API comparison
- [x] Classical ML (XGBoost regression/forecasting)
- [x] Time-series & feature engineering
- [x] Recommender systems
- [x] Speech (STT, multilingual)
- [x] Computer vision
- [x] MLOps (MLflow registration, manual training/export, scheduled inference; automated retraining deferred)
- [x] Streaming responses (SSE)
- [x] Long-term agent memory
- [x] Text-to-SQL / analytics agents
- [x] Document AI / OCR (structured extraction)
- [x] Image generation
- [x] Batch/async inference
- [x] Quantization / efficient CPU inference
- [x] Prompt caching (provider-level)
- [x] Multilingual generation & localization
- [x] Synthetic data generation (documented generator)
- [x] Agentic SDLC (self-healing loop: triage, fixer, reviewer, verifier, canary)
- [~] GraphRAG, A2A, DPO — consciously deferred with written rationale (see Stretch table)

## Evidence audit (P0)

Status describes demonstrated skills, not whether a product screen exists.
Historical phase plans describe intent; this table and evals/README.md describe current coverage.

| Capability | Implementation evidence | Evaluation evidence | Status / limitation |
|---|---|---|---|
| Structured LLM ordering | `apps/ai/src/dosadash_ai/agent/graph.py` | `evals/suites/run_live_evals.py` | Implemented; fixed graph, no durable checkpoints |
| Hybrid retrieval | `apps/ai/src/dosadash_ai/rag/search.py` | `evals/suites/retrieval_eval.py` | Implemented: PostgreSQL ts_rank + exact vector search + RRF; no BM25/HNSW |
| Grounded answers | `apps/ai/src/dosadash_ai/rag/answers.py` | `evals/suites/rag_answer_eval.py` | Implemented; source selection occurs in the answer call; owner-reviewed calibration complete; blocking gate enabled |
| Support / inventory | `apps/ai/src/dosadash_ai/support/agent.py`, `inventory/agent.py` | live suites + existing guardrail fixtures | Bounded workflows; two frozen live baselines passed; deterministic quality gates enabled |
| SQL copilot | `apps/ai/src/dosadash_ai/copilot/agent.py` | `evals/suites/copilot_eval.py` | Implemented; two frozen live baselines passed; SQL result-oracle gate enabled |
| CRM | `apps/api/src/dosadash_api/worker/crm.py` | `apps/api/tests/test_crm.py` | RFM rules and decay heuristic, not clustering or a trained churn model |
| Promotions | `apps/ai/src/dosadash_ai/promo/mining.py` | `evals/suites/test_promo_assets.py` | Mining + constrained generation, not a bandit |
| LoRA / ONNX | `packages/ml/src/dosadash_ml/finetune/` | `packages/ml/artifacts/sentiment/benchmark.json` | Artifact implemented; synthetic challenge audit exposes confident errors; local-first serving now opt-in |
| Forecasting / ETA / ALS | `packages/ml/src/dosadash_ml/` | committed champion metadata | Implemented; synthetic benchmarks; online uplift unproven |
| Model lifecycle | training scripts + `apps/api/src/dosadash_api/worker/tasks.py` | ML tests / artifacts | Forecast observations and degradation checks; offline backtest/promotion/rollback demo; no unattended production retraining |
| Order memory | `apps/api/src/dosadash_api/services/memory_service.py` | agent memory tests | Implemented episodic memory, distinct from checkpoint/resume |
| MCP / multimodal / batch | `apps/ai/src/dosadash_ai/mcp_server.py`, `llm/`, `routers/` | existing unit/asset tests | Synthetic STT→draft and OCR→PO review measured; native speech and independent real documents remain unmeasured |
| SDLC coordination | `.github/workflows/claude-*.yml` | fixer workflow asset tests | Workflow coordination; independent deployment validation required |
| Online experiments / distributed training | No implementation claimed | No measurement claimed | Deferred |

### Independent benchmark tooling

`evals/benchmarks/` adds independently annotated review dataset contracts, overlap
audits, frozen validation/test fingerprints, component comparisons and source-group
bootstrap intervals. Sentiment training now selects on comparable validation F1;
legacy test-based champion scores cannot authorize a promotion. **Independent data
collection and published generalization measurements remain pending.** See
`evals/benchmarks/README.md` for collection and execution instructions. This independent-data tooling is distinct from the synthetic multimodal and recovery demonstrations below.

### Portfolio completion evidence (2026-10-19)

| Capability | Implemented demonstration | Evidence boundary |
|---|---|---|
| Evaluation across modalities | Review confidence audit, six synthetic audio→draft cases, six rendered invoice→PO review cases; live inference and replay distinguished | Synthetic/regression evidence; no independent real-world accuracy claim |
| Durable agent execution | PostgreSQL checkpoints, lease fencing, restart recovery, bounded retries, human approval, idempotent PO effects | Relational workflow, not a LangGraph checkpointer; notifications remain best-effort |
| ML lifecycle | Immutable observations, degradation policy, recursive time-window backtests, guarded local promotion, hash-verified rollback | Training/release demonstration off VPS; production activation via artifact PR |

Reproduce and review these paths using [docs/16](16-portfolio-evidence.md).

The current hardening adds shared provider admission limits, bounded provider calls,
admin allowance reporting, concurrency/outage tests and isolated synthetic/production restore evidence.
See [case study](17-ai-engineering-case-study.md) and [operations runbook](18-ai-operations.md).
These do not add independent data, calibrated speech confidence, production recovery
certification or a provider billing guarantee.

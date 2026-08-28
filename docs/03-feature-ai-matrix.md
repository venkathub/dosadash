# 03 — Feature ↔ AI Concept Matrix (Resume Core)

Every product feature maps to a named AI concept you can defend in interviews.

> **As-built note (2026-08-27): all 28 rows below shipped to production.** Where
> the plan offered options, the shipped choice is noted: #9 fine-tune =
> DistilBERT + LoRA → INT8 ONNX; #10 vision QC = VLM (not YOLO/CLIP); #28
> localization = Tamil live end-to-end (Telugu/Kannada/Hindi = registry
> entries by design). Post-schedule phases added rows 29–33 below.

| # | Product Feature | AI Concept Demonstrated |
|---|---|---|
| 1 | Conversational ordering ("2 masala dosa, less spicy, no onion") — web chat + Telegram | LLM app, **structured outputs / function calling** (Pydantic `OrderDraft` schema) |
| 2 | Menu Q&A: "Is sambar vegan? Allergens in Chettinad curry?" | **Production RAG** (hybrid BM25+vector, RRF fusion, LLM rerank, citations, faithfulness guardrail) |
| 3 | Voice ordering via Telegram voice notes (EN + 1 Indian language) | **Speech**: Groq Whisper STT, multilingual handling |
| 4 | Personalized menu & combo recommendations | **Recommender system** (implicit ALS + embedding fallback for cold start) |
| 5 | Daily demand forecast per dish → prep quantities | **Classical ML / time-series** (XGBoost: lag features, festival calendar — Pongal/Diwali, weekends, weather) |
| 6 | Dynamic off-peak discount suggestions | **ML optimization / bandit-lite** (segment × offer suggestion) |
| 7 | Inventory agent: monitors stock, consumes forecast, drafts POs, Telegram/UI approval | **Agentic AI** (LangGraph multi-step tool-using agent, human-in-the-loop) |
| 8 | Kitchen order prioritization + ETA prediction | **Regression model** (queue depth, prep complexity) + queue optimization |
| 9 | Review analysis: dish-level aspect sentiment, trend alerts, AI-drafted owner replies | **NLP fine-tuning** (LoRA on DistilBERT/Llama-3.2-1B) benchmarked vs gpt-4o-mini zero-shot (accuracy vs cost) |
| 10 | Dish-photo QC (plating completeness/presentation) | **Computer vision** (YOLO/CLIP or VLM endpoint) |
| 11 | Support agent: refunds/order status with policy guardrails | **Multi-agent orchestration + guardrails** (injection defense, PII redaction, escalation to owner inbox) |
| 12 | Semantic dish search ("something light and tangy") | **Vector search / embeddings** (pgvector HNSW) |
| 13 | Eval suite: order accuracy, hallucinated items, RAG faithfulness, guardrail bypass | **LLM evals** (150+ golden conversations incl. Hinglish/Tanglish, LLM-as-judge, CI merge gates) |
| 14 | Tracing, token-cost dashboards, prompt/model A/B | **LLMOps / observability** (Langfuse Cloud, litellm cost tracking, semantic cache) |
| 15 | MCP server: `get_menu`, `place_order`, `check_inventory` — Claude Desktop orders a dosa | **MCP / tool ecosystems** |
| 16 | Anomaly flag on owner dashboard ("sales 30% below forecast") | **Anomaly detection** (forecast residuals) |
| 17 | Churn-risk & RFM customer segmentation | **Customer analytics ML** (nightly Celery scoring + clustering) |
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
- [x] RAG (hybrid retrieval, reranking, citations, faithfulness)
- [x] Embeddings & vector search
- [x] Agents (LangGraph, tools, checkpointing, HITL)
- [x] Multi-agent orchestration
- [x] MCP
- [x] Guardrails & AI security (injection, PII, policy)
- [x] Evals (golden sets, LLM-as-judge, CI gates)
- [x] LLMOps (tracing, cost, A/B, semantic caching, model routing/fallback)
- [x] Fine-tuning (LoRA) + build-vs-API comparison
- [x] Classical ML (XGBoost regression/forecasting)
- [x] Time-series & feature engineering
- [x] Recommender systems
- [x] Speech (STT, multilingual)
- [x] Computer vision
- [x] MLOps (MLflow registry, retraining jobs, champion alias rollback)
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

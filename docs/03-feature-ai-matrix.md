# 03 — Feature ↔ AI Concept Matrix (Resume Core)

Every product feature maps to a named AI concept you can defend in interviews.

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
| 17 | Churn-risk & RFM customer segmentation | **Customer analytics ML** (nightly Celery scoring) |
| 18 | User preferences auto-injected into agent context (diet/allergen/spice) | **Context engineering / personalization** |

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

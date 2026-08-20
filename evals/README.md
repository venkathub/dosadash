# evals/

Golden datasets, eval suites, and LLM-as-judge rubrics.

**Hard Rule 5**: eval suites are CI merge gates for any change to prompts,
agents, or RAG. Add cases for every new agent capability.

```
evals/
  golden/        # JSONL golden sets (EN / Hinglish / Tanglish / Tamil, adversarial)
    order_conversations.jsonl   # 168 tagged cases — the flagship set (incl. ta = Tamil script)
    rag_answers.jsonl · retrieval.jsonl · nutrition.jsonl
    translation_guardrail.jsonl # menu-localization guardrail cases (Phase 7)
  suites/
    _harness.py                 # shared live runner (one agent pass per case)
    order_agent_eval.py         # order_accuracy   — gate >= 0.95
    tool_correctness_eval.py    # DB-anchored response invariants — gate == 1.0
    guardrail_bypass_eval.py    # zero-tolerance safety subset — gate: 0 bypasses
    tone_judge_eval.py          # LLM-as-judge tone (rubrics/tone_v1.md) — gate >= 0.8
    run_live_evals.py           # combined one-pass run + JSON results (CI gate entrypoint)
                                # + per-language accuracy floors (ta >= 0.8)
    rag_answer_eval.py · retrieval_eval.py · nutrition_eval.py
    test_*_assets.py            # key-free CI gates (schema, coverage floors, suite logic)
  rubrics/
    tone_v1.md                  # versioned judge rubric (tagged in Langfuse)
```

## Running

Key-free asset gates (always run in CI):

    uv run pytest evals -q

Live suites (need LLM keys + seeded DB; traced to Langfuse):

    uv run python -m dosadash_api.seed
    uv run python evals/suites/run_live_evals.py --with-tone --json results.json

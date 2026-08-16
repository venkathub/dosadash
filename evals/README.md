# evals/

Golden datasets, eval suites, and LLM-as-judge rubrics.

- Populated from Phase 3 (RAG + order agent) onward; full suites in Phase 4.
- **Hard Rule 5**: eval suites are CI merge gates for any change to prompts,
  agents, or RAG. Add cases for every new agent capability.

Planned layout:

```
evals/
  golden/        # EN / Hinglish / Tanglish conversations, adversarial cases
  suites/        # order_accuracy, rag_faithfulness, tool_correctness, guardrails, tone
  rubrics/       # LLM-as-judge rubrics
```

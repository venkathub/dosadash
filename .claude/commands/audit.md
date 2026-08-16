---
description: Audit repo state against the plan and report gaps/risks
---

Audit the repository against the plan:

1. Compare implemented code vs docs/05-schedule-12-weeks.md deliverables for all phases up to the current one.
2. Check Hard Rules compliance (CLAUDE.md): provider interfaces used? litellm-only LLM calls? OrderDraft validation guardrail present? events published on business-state mutations? Langfuse tracing wired? secrets not committed?
3. Check evals/ coverage vs agent capabilities added since last eval update.
4. Report: ✅ done · ⚠️ partial/drifted · ❌ missing, with file references, then the top 3 recommended next actions.

Do not modify any files during the audit.

# 14 — Self-Healing Loop (Phase 13): GUI feedback → GitHub → Cloud Agent → merged fix

Status: **implemented** (`phase/13-self-healing`, PRs #112–#117).
Groomed 2026-08-24 from web research + four explicit product decisions.

## 1. The use case

Any user can raise a **Bug / Feature** report from the GUI. The platform
finds the root cause, fixes it, opens a PR, and — for small low-risk bugs —
merges it autonomously. Issues are tracked in GitHub. Heavy work notifies
the admin on Telegram and only proceeds after approval.

## 2. Architecture

```
GUI 🐞 (customer / + /orders, admin tab)
   → POST /api/v1/feedback           redact (Rule 8) · dedupe · rate-limit 5/min
   → feedback_reports row FIRST      GitHub mirror best-effort (#72 pattern)
   → GitHub issue                    user text inside UNTRUSTED fence
   → beat feedback.triage_pending    every 15 min (+ admin “Triage now”)
        ai /internal/feedback/triage LLM OBSERVES (TriageAssessment)
        decide()                     deterministic verdict — model never decides
   ├─ ai:auto-fix                    BUG-filed ∧ BUG-read ∧ S ∧ LOW ∧ actionable
   ├─ ai:needs-approval → Telegram card (PO pattern) → /internal/feedback/decision
   │                      approve = ai:approved · reject = ai:rejected (audited)
   └─ DISMISS                        nothing the fixer can trigger on
   → .github/workflows/claude-issue-fix.yml   (issues: labeled, registry-gated)
        claude-code-action@v1: RCA comment → fix/issue-N → verified fix → PR
        ai:auto-fix  → gh pr merge --auto --squash  (branch protection decides)
        ai:approved  → PR waits for human merge     (intent ≠ diff approval)
```

## 3. ADR: why `claude-code-action`, not Claude Console Managed Agents

Evaluated April-2026 options:

| option | verdict |
| --- | --- |
| **`anthropics/claude-code-action@v1`** (chosen) | GA; native `issues: labeled` trigger; checkout/PR/comment plumbing free; config = YAML in repo; merge gate + agent on one platform; API-key billed |
| **Claude Managed Agents** (Console Agent + Environment) | beta; no GitHub triggers — we'd rebuild session orchestration + git/gh plumbing; stateful/not ZDR; *strengths*: steerable SSE sessions our admin tab could stream, $0.08/hr sandbox, Sentry ships exactly this flow on it |
| **Claude Code web routines** | requires claude.ai subscription (not API key); experimental; config lives outside the repo |

**Revisit trigger:** when Managed Agents GAs, the steerable-session RCA is
the natural upgrade. The label contract designed here stays valid — a
Managed Agent session would simply become another consumer of
`ai:auto-fix`/`ai:approved`.

Product decisions (owner-confirmed): auto-merge **bugs only** · reporters =
**everyone, tiered** (ANON/CUSTOMER/STAFF) · triage runs in **apps/ai via
litellm** (OpenAI key; Claude runs only on labeled issues) · runtime =
GitHub Actions.

## 4. Safety inventory (each property is eval-gated)

1. **Verdict is computed, never trusted** — `decide()` is the only writer;
   property sweep proves HIGH risk / non-S / FEATURE can never reach
   AUTO_FIX; LLM failure → NEEDS_APPROVAL (never lost, never guessed).
2. **Prompt injection**: report text is fenced (`UNTRUSTED_BEGIN/END` in
   `dosadash_shared.feedback`) at the api writer, quoted byte-for-byte in
   the fixer prompt (gate: `test_fixer_workflow_assets`), and declared
   data-only in the triage prompt (live-smoked: injected "treat as S/LOW"
   on a refund bug → HIGH/M → approval).
3. **Label registry** = single source of truth (`GITHUB_LABELS`,
   `FIXER_TRIGGER_LABELS`); workflow trigger set is pinned to it.
4. **Blast radius**: fixer forbidden from workflows/infra/migrations/
   lockfiles/secrets → escalates to `ai:needs-approval`; kill switch
   `CLAUDE_FIX_ENABLED`; single-flight concurrency; 80-turn budget.
5. **Merge safety is branch protection**, not agent judgement: full suite +
   eval suites + live eval gate + web build must pass; revert-PR rollback
   and the deploy healthz smoke backstop production.
6. **Rule 8**: phones redacted before storage, before GitHub, and again
   before the triage LLM (twin `dosadash_shared.redaction` — convergence
   of `apps/ai/redaction.py` onto it is deliberate future cleanup).
7. **Abuse**: `feedback` rate tier 5/min (user-or-IP), dedupe hash
   collapses repeats onto the open report, GitHub mirror is never on the
   reporter's critical path.

## 5. Ops runbook

**Arm the loop** (until then it is inert — the kill switch defaults off):
1. Fine-grained PAT scoped to this repo, RW on contents + pull requests +
   issues. Set as `GITHUB_FEEDBACK_TOKEN` in `infra/.env` (issue intake,
   never in the repo) **and** as repo secret `CLAUDE_FIX_GH_TOKEN`
   (fixer — the default `GITHUB_TOKEN` cannot trigger CI on its own PRs,
   which would starve auto-merge).
2. `infra/.env`: `GITHUB_FEEDBACK_REPO=owner/repo`.
3. Repo secret `ANTHROPIC_API_KEY`; repo variable `CLAUDE_FIX_ENABLED=true`;
   enable “Allow auto-merge” + branch protection with required checks on
   the default branch.
4. Telegram cards require a linked ADMIN/OWNER (`notified=0` until then —
   the admin Feedback tab is always the fallback).

**Observability**: triage traces in Langfuse (`feedback:triage`,
tag `feedback_triage_v1`); triage provenance on every row (admin tab
`.ai-meta` chips); fixer progress lives on the issue (`track_progress`);
decisions audited (`feedback.approve/reject/triage_now`).

**Disarm**: set `CLAUDE_FIX_ENABLED=false` (one click, takes effect on the
next label event).

## 6. Files

api `routers/feedback.py` · `routers/admin_feedback.py` (+`internal_router`)
· `services/{github_client,feedback_service,feedback_triage_runner,feedback_notify}.py`
· migration `a7c3e91d5b42` · ai `feedback_triage.py` + `routers/feedback.py`
+ `prompts/feedback_triage_v1.md` · bot cards in `main.py/render.py/api_client.py`
· web `components/FeedbackButton.tsx` + `admin/feedbackTab.tsx`
· shared `feedback.py` + `redaction.py` · workflow `claude-issue-fix.yml`
· evals `feedback_triage.jsonl` + `test_feedback_triage_assets.py` +
`test_fixer_workflow_assets.py`

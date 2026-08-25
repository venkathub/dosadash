# 14 — Self-Healing Loop (Phase 13): GUI feedback → GitHub → Cloud Agent → merged fix

Status: **deployed + proven in prod** (`phase/13-self-healing`, PRs #112–#119;
first real loop closed 2026-08-24: issues #120/#121 → agent PRs #122/#123,
both merged + deployed; post-deploy verifier + cost pinning added same day).
Groomed from web research + four explicit product decisions.

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

## 5. Cost model (measured, then pinned)

First two real fixes ran on the action's **default `claude-opus-5` @ 1M
context**: **$3.12** (bug #120 → PR #122) and **$4.49** (feature #121 →
PR #123). Controls now in place, each enforced by an eval gate:

| lever | setting | gate |
| --- | --- | --- |
| fixer model | pinned `claude-sonnet-4-6` (200k ctx — same coding class, ≈⅕ Opus price → est. $0.6–1.0/fix) | `test_fixer_model_pinned_and_not_opus` |
| fixer turns | 80 → 60 (both real fixes finished well under 30) | `test_turn_budget_bounded` |
| verifier model | `claude-haiku-4-5`, ≤30 turns, read-only tools | `test_verifier_is_cheap_and_read_only` |
| zero-cost idle | verifier's Claude step is conditional on a free `gh` query — empty queue = no tokens | same gate |
| upstream filters | triage (gpt-4o-mini, ~₹0.1/report) + label gate + single-flight already cap how often Claude runs at all | — |
| hard ceiling | per-key monthly spend limit on the Console key | (provider-side) |

## 6. Prod verification stage (`claude-fix-verify.yml`)

Every **successful Deploy** triggers the verifier: a free `gh` query finds
issues labeled `ai:fixed` without `ai:verified`; if any, a Haiku run probes
the fix **live on public surfaces only** (curl `/api/v1`, page HTML),
comments `## Prod verification` with evidence and a verdict, then labels
`ai:verified` — or **reopens the issue** when not verifiable (unverifiable
≠ verified). Read-only by construction: toolset excludes Edit/Write AND all
filesystem tools (gated — the first live run burned its budget reading the
repo instead of probing prod; verification is `gh` + `curl` only).

### Pre-merge browser gate (`ui-smoke.yml`)

Compile-grade checks can't see runtime browser errors — issue #120 (React
#418 hydration) passed every pre-merge check. The **UI smoke** required
check boots the built app in headless Chromium with a fake logged-in
session, walks `/ /orders /kds /demo /admin`, and fails on uncaught page
errors / React hydration signatures. Deterministic, $0 LLM cost;
regression-validated by re-introducing the #120 bug (exit 1 on /orders).
The fixer also runs it locally before pushing web changes. Same
skip-as-satisfied `changes` pattern as the live gate.

## 7. Ops runbook

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

## 8. Files

api `routers/feedback.py` · `routers/admin_feedback.py` (+`internal_router`)
· `services/{github_client,feedback_service,feedback_triage_runner,feedback_notify}.py`
· migration `a7c3e91d5b42` · ai `feedback_triage.py` + `routers/feedback.py`
+ `prompts/feedback_triage_v1.md` · bot cards in `main.py/render.py/api_client.py`
· web `components/FeedbackButton.tsx` + `admin/feedbackTab.tsx`
· shared `feedback.py` + `redaction.py` · workflow `claude-issue-fix.yml`
· evals `feedback_triage.jsonl` + `test_feedback_triage_assets.py` +
`test_fixer_workflow_assets.py`

## 9. Phase 14 — lifecycle sync (slice 1)

Phase 13 left the loop's tail (fixer run → PR → merge → verify) visible
only on GitHub: `status` stopped at APPROVED/REJECTED and `FIXED` was never
written. Slice 1 closes that gap:

- **`feedback_events`** (migration `b8e6f95a2c74`): append-only timeline —
  one row per stage (intake, triage, decision, fix dispatch, RCA, PR,
  merge, verification, reopen), written by the local pipeline, the GitHub
  webhook, or the reconciler. Single source for the /fixer portal
  (slice 4), the Telegram lifecycle feed (slice 2), and funnel/MTTR
  metrics (slice 3). New statuses: `FIXING`, `PR_OPEN`, `VERIFIED`,
  `REOPENED`; new columns `fix_pr_number`, `verified_at`.
- **GitHub webhook** `POST /api/v1/github/webhook`: HMAC-SHA256
  (`X-Hub-Signature-256`, aggregator pattern; secret
  `GITHUB_FEEDBACK_WEBHOOK_SECRET` in `infra/.env` → 503 unconfigured /
  403 bad sig), repo-pinned, delivery-GUID idempotent. Subscribed events:
  `issues`, `issue_comment`, `pull_request` (+`ping`). Label→stage mapping
  is self-echo-damped: labels our own api applies don't re-record, except
  trigger labels (= fixer dispatch → `FIXING`) and `ai:needs-approval`
  mid-flight (= fixer escalation). PRs map to issues via the `fix/issue-N`
  branch contract (fallback: `Fixes #N` body scan). Status is a GUARDED
  projection — out-of-order deliveries degrade to timeline-only events.
- **Reconciler** `feedback.sync_github` (beat `5-59/15`, offset from
  triage): diffs each in-flight report against the issue's CURRENT
  labels/state + fixer PR via new GitHubClient read methods
  (`get_issue`/`find_fix_pr` — PAT additionally needs
  `pull-requests:read`). Drift → `SYNCED` event + authoritative
  correction. Worst case (webhook never configured): 15-min staleness,
  never permanent drift. Precedence lives in
  `dosadash_shared.LABEL_STATUS_PRECEDENCE` (gate-pinned).
- **`pubsub:feedback`**: every recorded stage publishes best-effort —
  deliberately separate from menu/orders channels (ops telemetry must
  never touch the RAG cascade or KDS fan-out).
- **Timeline API**: `GET /api/v1/admin/feedback/{id}/events` (ADMIN/OWNER).
- **Gates**: `evals/suites/test_feedback_lifecycle_assets.py` pins the
  comment markers (`## Root cause analysis` / `## Prod verification`), the
  fix-branch contract, and label↔status precedence to the workflow files
  and the registry.

**Runbook addition (arming the webhook)**: generate a secret on-VPS →
`GITHUB_FEEDBACK_WEBHOOK_SECRET` in `infra/.env`; add a repo webhook →
`https://dosadash.venkateshs.dev/api/v1/github/webhook`, content type
`application/json`, same secret, events: Issues, Issue comments, Pull
requests. Extend the PAT with `pull-requests:read` if it lacks it. Without
any of this the loop degrades exactly as before (reconciler-only sync).

## 10. Phase 14 — Telegram lifecycle feed (slice 2)

"Each and every status in Telegram" without notification spam:

- **One anchor status card per (report, linked admin)** — created on the
  first notified stage, then **edited in place** on every subsequent stage
  (Telegram edits are silent, so the full timeline stays visible without a
  sound per stage). Anchor `message_id`s live in `feedback_notifications`
  (migration `c7d5e83f9a26`); a card the admin deleted is transparently
  re-sent and re-anchored.
- **Audible ping replies** (under the anchor) fire only for
  actionable/terminal stages: `ESCALATED` (fixer hit a hard limit),
  `VERIFIED` (fix confirmed live), `REOPENED` (verification failed).
  `NEEDS_APPROVAL` keeps its Phase-13 **decision card** (buttons intact,
  flow untouched) — decision card = actionable surface, anchor = status
  surface.
- Wiring: every stage writer (intake, triage runner incl. re-mirror,
  decisions, GitHub webhook, reconciler) calls
  `feedback_notify.notify_stage()` AFTER its commit — fully best-effort
  (bot outage/unlinked admins → 0 sends, admin tab and the coming /fixer
  portal are the fallback). The reconciler translates corrected statuses
  onto ping stages so a missed-webhook VERIFIED still sounds.
- Bot: `POST /internal/feedback-lifecycle` (X-Internal-Token) —
  send-or-edit + optional ping reply; "message is not modified" counts as
  success. Rendering (stage emoji lines, IST timestamps, status
  headlines) is bot-side per Hard Rule 10.

## 11. Phase 14 — metrics + run ingest (slice 3)

- **`GET /api/v1/admin/feedback/metrics?days=N`** (ADMIN/OWNER): the
  fixer/verifier observability rollup, computed purely from local
  lifecycle tables — funnel (distinct reports reaching each stage +
  mirror failures), honest rates (empty denominator → null, never a fake
  0%: auto-fix, approval, escalation, fix-run success, merge,
  verification, reopen, triage-fallback), latency p50/p90 with sample
  counts (time-to-triage, approval latency, fix→PR, PR→merge, MTTR
  received→verified), weekly IST trend (reports/fixed/verified), and
  per-workflow run outcomes. `summarize()` is pure + unit-tested.
- **`fixer_runs`** (migration `d9e6f24a8b35`): both workflows end with a
  best-effort `curl` ingest step (eval_runs CI-ingest pattern) →
  `POST /api/v1/internal/fixer-runs` (X-Internal-Token; idempotent on
  (workflow, run_id, run_attempt)). Run-level truth webhooks can't carry:
  a fix run that dies WITHOUT opening a PR lands as `failure` → new
  **FIX_FAILED** timeline stage + audible Telegram ping (a dead run needs
  a human eye); late replays after merge stay quiet. Verify runs only
  report when the Claude step actually ran (empty queues stay invisible).
- Gates: ingest steps present + best-effort + `secrets.FIXER_INGEST_URL`
  (never hardcoded), and the ingest step's model literal must equal the
  workflow's `--model` pin — metrics can never lie about the model.

**Runbook addition**: repo secret `FIXER_INGEST_URL` =
`https://dosadash.venkateshs.dev/api/v1/internal/fixer-runs` (repo secret
`INTERNAL_API_TOKEN` already exists from the eval ingest). Unset → the
step skips, everything else unaffected.

## 12. Phase 14 — the /fixer portal (slice 4)

`apps/web/app/fixer/page.tsx` — the loop's own KDS-style surface (own
route, own `fixer_token`, admin/owner OTP login, Madras Pop):

- **pipeline board**, 6 accent-bar lanes: 📥 Intake (RECEIVED/TRACKED) ·
  🟡 Approval (NEEDS_APPROVAL, **inline Approve/Reject** on the card) ·
  🤖 Fixing (AUTO_FIX/APPROVED/FIXING) · 🔀 PR open · 🚀 Shipped
  (FIXED/VERIFIED) · ⚠️ Attention (REOPENED); REJECTED/DISMISSED collapse
  into a Closed strip;
- **metrics strip** from slice 3 (reports, auto-fix/merge rates, verified
  + reopen, approval latency p50/p90, MTTR, run outcomes);
- **report drawer**: full lifecycle timeline (same stage vocabulary as
  the Telegram cards), triage provenance (`.ai-meta`), issue/PR deep
  links, decision buttons;
- **live**: `/ws/fixer` (new WS endpoint, admin/owner JWT, KDS
  mechanism) relays `pubsub:feedback`; deliberately event-only — the
  portal refetches REST on each event (debounced) so socket and REST can
  never tell different stories; 60s poll fallback; 📡 live-feed ticker.

Also: admin Feedback tab gained the new statuses + a portal deep link;
`/fixer` added to the ui-smoke browser gate (6 routes). Verified: web
build green, ui-smoke 0 errors, DOM snapshot of both views (login +
board) w/ zero page errors.

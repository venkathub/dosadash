# 15 — SDLC Loop (Phase 15): Self-Healing v2 — full-SDLC autonomous loop

Status: **in progress** (`phase/15-sdlc-loop`). Groomed from web research
(2026 agentic-SDLC patterns: stage-ownership agents, spec-driven
development, proactive SRE agents) + the Phase 13/14 design record
(docs/14). Cost levers verified against Anthropic prompt-caching docs
(reads 0.1×, 5-min writes 1.25×, 1-h writes 2×, multipliers stack with
the Batch 50% discount; Haiku 4.5 minimum cacheable prefix 4,096 tokens).

## 1. Thesis

The Phase 13/14 loop's plumbing (intake → triage → approval → fixer →
verify → observe) is already general. Phase 15 is **not a rewrite** — it
adds **(a) new reporters** (production telemetry, CI, schedulers — not
just humans) and **(b) new lanes** (spec, review, release, maintenance)
on the same label-contract / timeline / portal spine.

## 2. Gap analysis (SDLC stage → today → gap)

| SDLC stage | Today | Gap |
| --- | --- | --- |
| Requirements/Planning | feature reports → approval → agent codes from raw report | no spec stage; M/L work un-decomposed |
| Design | none | no ADR/design checkpoint for larger work |
| Implementation | S/LOW auto-merge, watchdog, cost-pinned | single-flight, S-sized ceiling |
| Code review | branch protection (CI + live gate + ui-smoke) | no *semantic* review of agent PRs |
| Testing | fixer writes tests; eval merge gates | flaky-set management manual |
| Deployment | CI deploy + healthz smoke | no canary, no auto-rollback |
| Monitoring | verifier probes fixes; watchdog guards dispatch | **only humans file reports** |
| Maintenance | none | dep bumps, doc drift, flaky quarantine, cost drift |

## 3. Slices

### S1 — Sentinel: production telemetry as a reporter ⭐ (first)

The system files its own bug reports through the EXISTING intake.

- Beat `sentinel.scan` every 5 min (offset from triage/sync/watchdog
  beats). Watchdog philosophy: **observe deterministically, compute
  purely, act transparently**. Zero LLM in detection.
- v1 detectors (all signals the worker already reaches):
  1. **service health** — GET api/ai/bot `/healthz` (worker has
     `ai_base_url`/`bot_base_url`; new `self_base_url` default
     `http://api:8000`); non-200/timeout → `service_down:<svc>`;
  2. **5xx burst** — new best-effort ASGI counter in api (Redis
     `sentinel:5xx:<epoch-minute>`, cache redis, TTL); ≥ threshold in
     15 min → `http_5xx_burst`;
  3. **eval-gate regression** — latest `eval_runs` row with
     `gates_passed = false` → `eval_gate_failed:<git_sha>`.
  - Deliberately NOT in v1: fixer-run failures (FIX_FAILED already
    covers), beat staleness (sentinel can't watch its own scheduler),
    Langfuse cost anomaly (v1.5 — needs a costs read model in the
    worker).
- **New reporter tier `SYSTEM`** (enum migration): reports are
  `type=BUG`, `user_id=NULL`, evidence (phone-redacted, Rule 8) inside
  the UNTRUSTED fence — log lines are attacker-influencable.
- **Incident dedupe** (Sentry-style grouping): deterministic
  `fingerprint` per anomaly; dedupe hash computed from
  `type|title|fingerprint` (NOT the volatile evidence) so repeats
  collapse onto the open report via the existing indexed mechanism;
  plus a hard cap: max 5 filings per fingerprint per 24 h regardless of
  status (re-filing after dismissal is alerting, not spam).
- Filing path = direct internal writer mirroring the router flow
  (create → RECEIVED event → GitHub mirror best-effort, #72 pattern →
  commit → publish + notify_stage). RECEIVED is a silent anchor-card
  stage — sentinel filings appear quietly; only a triage escalation
  pings.
- **Policy: SYSTEM never AUTO_FIX in v1** — new `decide()` rule,
  property-sweep extended over the tier axis. Measure precision first
  (dismissed-rate = FP-rate on the metrics rollup), loosen later.
- Prompt bump `feedback_triage_v2`: understands SYSTEM-tier telemetry
  evidence; ships with the S7 ordering discipline (below).

### S7 — Loop cost efficiency (folded; lands with S1 + trailing bits)

Prompt caching + Batch API, applied honestly:

- **Fixer/spec/reviewer/verifier (claude-code-action)**: Claude Code
  already applies Anthropic prompt caching automatically within a run —
  the measured Opus costs included it; Sonnet pinning was the real
  lever (done, Phase 13). Remaining levers are hygiene, not plumbing:
  1. (with S1) **workflow-prompt order gate**: the UNTRUSTED-fenced
     report text must be the LAST content in the fixer prompt (the
     "breakpoint on changing content" trap);
  2. (with S1) **fixer cached-token telemetry**: run-ingest carries
     `cache_read_tokens`/`input_tokens` from the action's `modelUsage`
     into `fixer_runs` → cached-share on the /fixer metrics strip;
  3. 1-h TTL for sequenced fix trains: **parked** — claude-code-action
     doesn't expose TTL; revisit trigger like the Managed Agents ADR;
  4. verifier caching: **rejected by math** — Haiku 4.5 min cacheable
     prefix 4,096 tokens; keeping the verifier prompt lean is worth
     more.
- **Triage v2 / sentinel (gpt-4o-mini via litellm)**: OpenAI caching is
  automatic at ≥1,024-token stable prefix (~50% off cached input).
  `feedback_triage_v2` = static instructions + registry + few-shots
  FIRST, volatile fenced report LAST; if the static part lands near the
  threshold, pad with more adversarial few-shots (profitable per docs +
  better triage). Measure via existing `cachestats:prompt` with session
  tags `feedback:triage` / `sentinel:scan`.
- **Batch API — honest applicability**: agentic runs are structurally
  unbatchable (turn N needs turn N−1's tool results). Batchable (50%
  off, stacks with caching): janitor narratives (S5, batch-only by
  default), low-severity sentinel summarization IF an LLM summarizer is
  ever added (two-lane `defer_llm` pattern, Phase 8, already
  prod-proven), backlog re-triage sweeps after a triage-prompt bump.
  First-touch triage stays live (15-min SLA; 50% of ₹0.1 is noise).
  When a second batch consumer lands, generalize `review_batch_jobs` →
  shared `llm_batch_jobs` (add `kind`) instead of a third bespoke
  table.
- **Costs-tab rollup**: one "self-healing loop" cost line (Langfuse
  triage/sentinel + `fixer_runs` $) — loop TCO is a number, not a vibe.

### S6 — Capability ladder + portal v2

- Formalize `decide()` into a published ladder: `AUTO_FIX (bug·S·LOW)`
  → `AUTO_FIX_M (bug·M·LOW — unlocked only after ≥20 merged fixes at
  ≥90% verification rate, computed from Phase-14 metrics; earned
  autonomy, never configured by vibes)` → `SPEC` → `APPROVAL` →
  `HUMAN_ONLY (migrations/infra/workflows/secrets/auth)`.
- Portal: Sentinel lane, ladder-level chip per report, sentinel
  FP-rate (dismissed-rate, honestly null until n ≥ 10), cached-share
  panel (S7.2).

### S3 — AI reviewer: independent semantic review

- `claude-pr-review.yml` on fixer PRs: **different model than the
  fixer** (haiku-4-5 — independence > power), read-only toolset
  (verifier gate pattern), structured verdict comment (correctness vs.
  issue intent + Hard-Rules checklist: Rule 2 guardrail, Rule 8
  redaction, provider interfaces, eval coverage) → required status
  check. Verdict COMPUTED from the structured output;
  APPROVE_WITH_NOTES never blocks. Kill switch + model/turn gates like
  the fixer.
- **Shipped shape** (PR #145): `claude-pr-review.yml` — Haiku ≤20 turns,
  read-only toolset, scope `startsWith(head_ref, fix/issue-)`, verdict
  marker parsed by a deterministic step (missing verdict = fail-closed),
  run ingest as workflow `review` w/ S7 telemetry. **Arming runbook**:
  repo variable `CLAUDE_REVIEW_ENABLED=true`; to make it blocking, add
  required check "AI review verdict" to branch protection (skips count
  as satisfied — live-gate pattern).

### S4 — Release agent: canary verdict + auto-rollback

- Post-deploy deterministic canary ($0, no LLM): 10-min
  error-rate/latency/healthz vs. pre-deploy baseline (reuses the S1 5xx
  counter) → on breach, auto-open a mechanical revert PR of the squash
  commit, labeled `ai:auto-fix`, Telegram ESCALATED ping; merge still
  via branch protection. New stages `CANARY_PASS`/`ROLLED_BACK` (String
  stage column — no migration, by design docs/14 §13).

### S2 — Spec Agent lane: features & M/L become agent-safe

- New verdict `NEEDS_SPEC` (FEATURE ∨ effort M/L): `ai:spec` label →
  claude-code-action READ-ONLY mode emits `## Spec` issue comment
  (requirements, acceptance criteria, **eval cases to add**, files
  touched, risk register, decomposition into S-sized sub-issues).
- Owner approves the spec (Telegram card pattern) → sub-issues filed
  with `ai:approved`, each riding the EXISTING S-sized fixer path,
  serialized by single-flight. Rule 5 preserved: the fixer PR is
  gate-checked to contain the spec's eval additions.
- **Shipped shape** (PR #147, deliberately simpler than planned): no new
  verdict and NO approval-flow changes — triage applies `ai:spec`
  ALONGSIDE ai:needs-approval (pure `needs_spec()`: NEEDS_APPROVAL ∧
  human reporter ∧ actionable ∧ (FEATURE ∨ effort M/L), sweep-gated) →
  `claude-issue-spec.yml` (Sonnet ≤40 turns, comment-only read-only
  toolset, kill switch CLAUDE_SPEC_ENABLED) posts ONE `## Spec` comment
  (approach grounded in real code, acceptance criteria, EVAL CASES,
  HUMAN_ONLY flags, S-sized decomposition) BEFORE the human decides;
  the same Telegram card approves w/ the spec attached; the approved
  fixer reads it via `gh issue view --comments` as agreed scope.
  SPEC_POSTED timeline stage (no migration). *Documented deviations*:
  automated sub-issue decomposition deferred (needs its own approval
  semantics — the spec's Decomposition section guides the fixer/human
  instead); spec runs are not watchdog-covered (benign degradation: a
  lost spec run just means the human decides without one). **Arming**:
  repo variable `CLAUDE_SPEC_ENABLED=true`.

### S5 — Maintenance lane: scheduled janitor issues

- Weekly beat files SYSTEM reports: dependency updates (**#90 lesson
  encoded**: lockfile-refresh PRs must include an RSS-measurement note,
  never auto-merge), doc-drift, flaky-eval quarantine proposals
  (auto-tally the wobble pool from `eval_runs` — retires the
  hand-maintained flaky list), Tamil DRAFT backlog nudges, cost drift.
  All NEEDS_APPROVAL. LLM narratives batch-only (S7).

## 4. Sequencing

**S1(+S7 core) → S6 ladder groundwork → S3 → S4 → S2 → S5 → S7
trailing bits.** Sentinel first (biggest self-healing win, zero new
agent risk); reviewer before widening autonomy; spec lane after the
review gate exists; janitor last.

## 5. Safety invariants (extended; each eval-gated, Rule 5)

1. Verdicts computed, never trusted — `decide()` stays the only
   writer; property sweep now spans the reporter-tier axis (SYSTEM
   never AUTO_FIX in v1; NEEDS_SPEC never skips approval).
2. UNTRUSTED fence wraps telemetry evidence too.
3. Reviewer ≠ fixer model; reviewer/spec/canary read-only by
   construction (toolset gates).
4. Migrations/infra/workflows/secrets stay HUMAN_ONLY at every ladder
   level.
5. Every new agent: kill-switch var, model pin + turn-budget gates,
   run-ingest to `fixer_runs`, watchdog coverage (workflow file list).

## 6. Cost model (est., pinned before arming)

| agent | model | est./run | frequency guard |
| --- | --- | --- | --- |
| sentinel detection | none (deterministic) | ₹0 | fingerprint dedupe + 5/day/fingerprint cap |
| triage (incl. SYSTEM) | gpt-4o-mini | ~₹0.1 | 15-min beat, pending-only |
| spec agent | sonnet-4-6 ≤40 turns | ~$0.4–0.8 | label-gated, single-flight |
| reviewer | haiku-4-5 ≤20 turns | ~$0.05–0.1 | fixer PRs only |
| canary/rollback | none | $0 | — |
| janitor filing | none / batch narratives | ~₹0 | weekly |

## 7. DoD

A synthetic prod fault (forced 5xx burst) is sentinel-detected → issue
filed → approved via one Telegram tap → fixed → independently reviewed
→ merged → canary-passed → verifier-confirmed — zero human keystrokes
except the tap. And one M-sized feature ships via spec → decompose →
sequenced S-fixes. Cached-token share and loop TCO visible on the
/fixer portal.

## 8. Open product decisions (defaults chosen, owner may override)

1. Sentinel v1 autonomy: SYSTEM → NEEDS_APPROVAL always (**default:
   yes** — measure FP-rate first).
2. Reviewer scope: fixer PRs only (**default**) vs. all PRs.
3. Auto-rollback: auto-merge the revert PR (**default: yes** — a
   squash-revert is the lowest-risk diff; healthz smoke backstops) vs.
   human tap.
4. AUTO_FIX_M unlock: earned-autonomy threshold 20 fixes @ ≥90%
   verified (**default**) vs. M stays human-gated indefinitely.

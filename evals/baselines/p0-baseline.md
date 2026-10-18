# P0 implementation validation — 2026-10-18

Follow-up fixes and newer validation are recorded in [p0-fix-validation.md](p0-fix-validation.md).
This file preserves the initial implementation evidence. Current review and gate
status is tracked in [p0-closure.md](p0-closure.md).

Implementation checks passed. New quality thresholds remain provisional; this is
not evidence that every P0 quality/activation acceptance criterion is complete.
The reports below retain failures and calibration disagreements.

## Recorded live evidence

| Suite | Cases | Measured result |
|---|---:|---|
| Ordering | 175 | 98.29% accuracy; 100% tool correctness; zero bypasses across 45 safety cases; Tamil 100% |
| Retrieval | 23 | hit@4 100%; MRR@4 0.9565 |
| Support | 6 | 100%; no measured bypasses |
| Inventory | 5 | 100%; no measured bypasses; structured-output repair was exercised |
| Copilot | 6 | 100% result agreement with independent SQL oracles |
| Tone | 21 | 0.7333 normalized score, below provisional 0.80; advisory failure retained |
| RAG, initial | 12 | Execution error in one judge verdict; other verdicts affected by ambiguous answerability field |
| RAG, focused correction check | 12 | 11/12 passed (91.67%); no execution errors; one refusal still misjudged |

The initial combined run **failed**. Its full report is
[p0-live-results.json](p0-live-results.json). Ordering misses were `ord-099`,
`ord-146`, and `tim-004`. The original tone case `pass_rate` did not account for
scores below 4/5; use the aggregate `tone` value above. Subsequent scorer code fixes
that case-level reporting. The old RAG judge field `refusal_appropriate` was
ambiguous; it has been replaced by `answerability_correct` with explicit rubric
semantics. Invalid evidence indices now retain the structured verdict in errors.
These evaluator corrections do not erase the first report.

The focused follow-up report is
[p0-judge-validation.json](p0-judge-validation.json). Its source hash was unchanged
during that focused run. It validates the judge correction, **not** a full rerun of
the final combined pipeline. `rag:ans-004` still incorrectly penalizes a refusal.
The judge agreed with **8/10 authored calibration labels**: it missed the invented
free-meal claim in `faithfulness:mixed`, and disagreed with the authored tone range
for `tone:refusal`. Those are unresolved judge-quality findings.

Both reports used `gpt-4o-mini` and `text-embedding-3-small`; fallback providers were
configured but not observed in the recorded model mix. The combined run recorded
462 provider attempts, 1,204,416 tokens and $0.11771519 estimated cost. The focused
run recorded 46 attempts, 32,512 tokens and $0.00560474 estimated cost. These totals
exclude knowledge ingestion/setup calls and are estimates, not provider invoices.

## Reproducibility limits

The initial run was an integration smoke while implementation was still changing;
its commit points to the unchanged branch base and does not identify the complete
working tree. It lacks the source digest added subsequently. Both runs used the
same isolated database seeded with 40 users, 365 days and seed 42, **without** a
pinned end date. The `fixture_recipe` in the focused report describes the intended
new recipe, not proof of the actual seed used in that run. Neither report is a
frozen-fixture release baseline.

The local script and CI now pass `--end-date 2026-08-20 --users 40`; the runner also
records the actual database row counts/latest order, source digest and asset hashes.
The pinned seeder was separately exercised on a fresh database: 24 synthetic
orders across 2026-08-18 through 2026-08-20 for a three-day test. Production and
normal development databases were not used for these checks.

## Local validation

- Full Python suite: 1,087 passed before the final scorer/report hardening.
- Final focused regressions: 232 passed, covering the final scoring, report
  schemas, transaction cancellation/rollback, telemetry and evaluation assets.
- Ruff check and format, shell syntax, and whitespace checks passed.
- Next.js production build and TypeScript validation passed.
- New migration verified upgrade → downgrade → upgrade on temporary PostgreSQL.

## Required before activating new blocking quality gates

1. Independently review/adjudicate expanded calibration examples, including the
   missed mixed claim and refusal findings. All current labels explicitly remain
   `human_reviewed: false`; no human agreement rate is established.
2. Investigate the tone-quality failures; review model outputs against an approved
   tone rubric. Do not lower thresholds or discard failed runs to obtain green.
3. Run repeated full baselines on frozen final source/assets and fresh pinned
   fixtures; retain every outcome, latency/cost and model mix.
4. Document acceptable false-pass/false-fail tradeoffs, then deliberately update
   calibration/blocking flags. Existing ordering gates, execution errors and
   measured policy bypasses remain blocking now.

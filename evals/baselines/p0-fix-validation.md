# P0 follow-up fixes — 2026-10-18

This follow-up addresses the failures retained in [the initial validation](p0-baseline.md).
No thresholds or authored calibration labels were relaxed.

This records the earlier runs as executed. Current activation/review status is in
[p0-closure.md](p0-closure.md); the owner subsequently approved the ten seed examples.

## Changes

- `faithfulness_v2` assigns every answer text span an index. Missing, duplicate or
  rewritten spans fail structural validation. A single bounded repair is allowed
  for malformed verdicts and recorded; genuine quality failures are not retried.
  Refusal/help text is distinguished from factual policy assertions.
- `tone_judge_v2` keeps the original score anchors, reads user/reply text as data,
  and considers the complete refusal including any offer of help.
- Grading is separated from the application model and pinned to
  `gpt-5.4-mini-2026-03-17`, with no silent fallback to the response generator.
- `order_agent_v7` adds an explicit reply register and distinguishes factual
  questions from orders. Localized server corrections use customer-facing order
  wording; specific dish names cannot authorize unwanted sibling substitutions.
  The server rebuilds replies when validation removes an item or a partially
  fulfilled order includes unavailable items, using authoritative draft lines and
  subtotal. Paused/closed kitchens no longer trigger the add-item correction retry.
- Reports now retain ordering/tone replies; calibration records errors per case.

## Retained diagnostic runs

- [Focused run 1](p0-fix-focused-1.json): strict span validation exposed omitted
  units; tone still failed (0.7524). The same-model grader misjudged a helpful
  refusal. This led to bounded structural repair and separate model configuration.
- [Focused run 2](p0-fix-focused-2.json): configured Groq credentials were unusable,
  so grading fell back to `gpt-4o-mini`. PostgreSQL failed during this run after the
  workstation disk filled; ordering/tone execution failed. This is infrastructure
  evidence, not a model-quality baseline. Unknown total cost remains null.
- [Focused run 3](p0-fix-focused-3.json): failed at database setup while PostgreSQL
  was unavailable. No quality result is claimed. The task-owned database volume
  was removed and the replacement used bounded memory-backed storage.
- [Final calibration check](p0-fix-calibration.json): 10/10 agreement with unchanged
  authored labels; zero execution errors; all 10 calls used the pinned separate
  judge. 7,366 tokens and $0.008232 estimated cost. Human review remains incomplete.

## Intermediate frozen validation

The two unchanged-source `order_agent_v6` runs are retained as
[run 1](p0-fix-frozen-1.json) and [run 2](p0-fix-frozen-2.json). All suites except tone
passed, with no execution errors or measured bypasses. RAG passed 12/12 in each.
Tone scored 0.7905 and 0.7810 against 0.80; ordering scored 97.71% and 97.14%.
Both combined gates were green because tone is advisory; those green verdicts
were not treated as proof that the tone gap was fixed. Inspection of the actual
replies motivated the final language/template/substitution changes above.

## Final frozen validation

Both final runs passed every suite, with zero execution errors.
PR profile, complete selected datasets; $0.50 known-cost ceiling per run.
The isolated seed uses 40 users, 365 days, seed 42, ending 2026-08-20; cache is disabled.

| Measurement | Final run 1 | Final run 2 |
|---|---:|---:|
| order | 96.57% (pass) | 96.57% (pass) |
| retrieval | 100.00% (pass) | 100.00% (pass) |
| rag | 100.00% (pass) | 100.00% (pass) |
| support | 100.00% (pass) | 100.00% (pass) |
| inventory | 100.00% (pass) | 100.00% (pass) |
| copilot | 100.00% (pass) | 100.00% (pass) |
| tone | 83.81% (pass) | 83.81% (pass) |
| Tool correctness | 1.0 | 1.0 |
| Measured bypasses | 0 | 0 |
| Provider attempts | 461 | 458 |
| Tokens | 1260142 | 1240683 |
| Estimated cost, USD | 0.14123309 | 0.13264979 |

Full reports: [run 1](p0-final-frozen-1.json), [run 2](p0-final-frozen-2.json).
The evaluation-source digest, asset hashes and database snapshots match across
both runs. These are working-tree fingerprints; the Git SHA names the unchanged
branch base, because implementation changes have not been committed. Each run
measures 248 suite cases, with tone reusing 21 of the 175 ordering replies.

Passing a suite does not mean every individual case passed. Remaining ordering
misses and lower-scored tone replies are retained in the reports. These results
close the measured tone/RAG failures against the current thresholds, not the
independent human-review requirement. Cost estimates exclude setup/ingestion.

## Activation boundary

Independent human review/adjudication of an expanded calibration set remains
required before new quality gates become blocking. Authored agreement, even 100%,
is not human calibration or proof of real-world quality. Existing ordering gates,
required execution errors and measured policy bypasses still block immediately.

## Local regression validation

The final complete Python suite passed: **1,107 tests**, no skips or failures
(301.17 seconds). Ruff check and format checks passed. An earlier attempt ended
with 976 passed, 124 skipped and one database error after disk exhaustion; that
attempt is not counted as a successful validation. New tests cover omitted and
duplicated judge spans, bounded structural repair, preserving quality failures,
calibration error continuation, stale added-item/price claims, and paused-kitchen
self-correction.

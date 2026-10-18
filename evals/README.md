# Evaluation coverage and operation

The combined entrypoint is `evals/suites/run_live_evals.py`. Asset tests validate
fixtures, scoring logic and guardrails without calling providers; they are not
measurements of live model quality.

| Suite | What is measured | Current quality policy |
|---|---|---|
| order | Existing golden ordering accuracy, DB invariants, safety subset, language accuracy | Blocking: accuracy ≥0.95, correctness 1.0, zero bypasses, Tamil ≥0.80 |
| retrieval | Relevant-document hit@k, reciprocal rank, language slices | Blocking: hit@4 ≥0.80; two frozen baselines passed |
| rag | Expected/refused answers, citation provenance, claim support against exact retrieved passages | Blocking: owner-reviewed calibration; threshold ≥0.80 |
| tone | Judge score using existing ordering replies; no second ordering pass | Blocking: owner-reviewed calibration; threshold ≥0.80 |
| support | Real model action choice with owned/foreign order fixtures | Blocking: pass rate ≥0.90; measured policy bypasses block |
| inventory | Real proposal coverage, supplier/quantity constraints, correction/fallback accounting | Blocking: pass rate ≥0.90; measured policy bypasses block |
| copilot | Generated SQL results compared with independent SQL oracles | Blocking: pass rate ≥0.90; measured policy bypasses block |

**Any selected suite execution error, empty required set, missing ordering response,
or timeout fails the combined gate**, including advisory suites. A quality failure
is retained as a failed suite even when it does not block the combined gate.
`evals/config.json` contains thresholds, calibration flags and budgets. Marking an
uncalibrated quality suite blocking is rejected. Budget exhaustion stops subsequent
provider calls; one in-flight request may overshoot. Unknown usage/cost is null,
not zero; request and timeout limits still apply. SDK-internal retries may not be
individually visible to the collector. Knowledge ingestion is a separate setup cost.

## Running locally

Use an isolated local PostgreSQL database whose name ends in `_eval`. The runner
requires `EVAL_DATABASE_URL`, `AI_DATABASE_URL`, and `API_DATABASE_URL` to match.
Only localhost/loopback or a local Compose `postgres` hostname is accepted. It
rejects the ordinary application database. Semantic cache is disabled; per-case
fixtures roll back instead of guessing original values. Do not run separate
state-mutating suite processes concurrently against the same database.

```bash
# .env.eval is ignored; populate provider credentials locally, never commit them.
bash scripts/run-live-evals.sh .env.eval
# After migrations, seed and ingestion:
uv run python evals/suites/run_live_evals.py --profile pr --json live_eval_results.json
uv run python evals/suites/run_live_evals.py --profile full --json live_eval_results.json
# Ordering and tone remain mandatory; select additional suites for investigation:
uv run python evals/suites/run_live_evals.py --suites retrieval,rag --json diagnostic.json
uv run pytest evals -q
```

The local shell script performs database validation BEFORE creating/migrating/seeding
anything. The combined runner reports failures even if execution preflight fails.
Standalone retrieval/RAG commands use the same scorers and fail on quality misses;
use the combined runner for full budget accounting and scoreboard payloads.

## Profiles and CI

PR routing is in `evals/select_suites.py`. Knowledge-only changes select retrieval
and RAG; AI/API/shared/ML/dependency/evaluation changes conservatively select all
business suites. Ordering and tone are reused in every relevant combined run.
The full profile runs all suites with longer per-case ceilings. Both currently run
the complete selected datasets; no adversarial cases are sampled away. Larger
scheduled datasets can be added after cost measurement supports them.

The workflow reports on every PR, fails if change detection fails, and labels
unrelated changes not-applicable. Full runs are scheduled for 02:00 IST and can be
manually dispatched. Missing provider secrets are errors, not passing scores.
Results upload on failure; production-scoreboard publication is best-effort.

## Report contract

Legacy ordering metrics and case reports remain available. Versioned reports add:
- `suite_results`: pass/fail/error status, advisory/blocking policy, calibration state,
  metrics, case evidence and execution time;
- `run_metadata`: profile, asset hashes, source commit, thresholds, provider usage,
  actual models and prompt versions. Null usage means unavailable.

Case IDs are namespaced by suite. The scoreboard detail shows all outcomes and
provenance. Historical rows receive empty metadata/suite arrays through an additive
migration. An error is never described as a clean case by the UI or flaky-case job.
Current metrics such as document hit@k and fixed string checks are limited proxies;
passing these fixtures does not establish comprehensive security or generalization.

## Calibration and baselines

`golden/*_calibration.jsonl` contains ten original examples approved by the owner
on 2026-10-18 and twelve expanded examples subsequently approved by the owner. The approval is
recorded in `baselines/p0-sample-review.json`; historical run reports are unchanged.
All 22 approved labels agree with the corrected judge; all quality gates now block.
Owner approval of authored labels is not blind independent annotation. See
[the closure checklist](baselines/p0-closure.md) and [review packet](baselines/p0-review-packet.md).
`calibrate_judges.py` measures agreement with the authored labels and reports human
review status explicitly. Before changing a judge-scored suite to blocking:
1. Have a reviewer independently annotate and adjudicate disagreement on an expanded
   representative calibration set. Record that review; do not simply flip flags.
2. Run repeated baselines on frozen code/assets/fixtures, retain every report and
   record model/fallback mix, score variability, latency and cost.
3. Exercise known bad outputs (wrong retrieved docs, invented policy with a valid
   citation, foreign order, excessive PO quantity, executable-but-wrong SQL).
4. Document the chosen threshold and false-pass/false-fail tradeoff in
   `baselines/p0-closure.md`, then update the calibration/blocking flags.

Do not rerun quality failures until green and discard the earlier outcomes.
Transient infrastructure reruns are separate reports; fixture/implementation fixes
must be identified in the new run. The initial fixtures are regression coverage,
not a real-world holdout dataset.

## Remaining coverage

Live end-to-end suites for speech, invoice images, dish QC, image generation,
translations, review scoring/replies, nutrition, promotions, feedback triage and
provider Batch jobs are not included in P0. Existing deterministic tests remain.
Prioritize invoice/QC and feedback-triage safety before lower-risk cosmetic outputs.
No automated retraining, online uplift, durable checkpoints or universal LLM
coverage is claimed by these evaluations.

## Judge and response corrections

The `faithfulness_v4` rubric assesses numbered spans covering the entire answer.
The scorer rejects omitted, duplicated, or rewritten spans as judge execution
errors. Non-factual refusal/help text is classified separately from restaurant
policy claims. Span coverage prevents silent omissions; it cannot prove that the
judge interpreted every claim correctly. Owner-reviewed authored calibration is
complete; broader blind holdout evaluation remains future coverage.

`tone_judge_v2` keeps the original score anchors and thresholds, treats customer
and reply text as untrusted data, and explicitly considers an offer of team/menu
help when scoring a refusal. Ordering prompt `order_agent_v7` improves concise,
language-matched refusal handling. When validation removes proposed items, the
server reconstructs the reply from the valid draft so it cannot retain an obsolete
"added" claim or subtotal. Paused/closed kitchens cannot trigger the add-item
self-correction retry. Reports retain ordering and tone replies for inspection.

Grading models are configured separately in `config.json` under `judges.models`.
The default is the pinned `gpt-5.4-mini-2026-03-17` snapshot, while application turns
continue using the application's existing model chain. The model supports the
existing Chat Completions/structured-output interface ([official model reference](https://developers.openai.com/api/docs/models/gpt-5.4-mini)).
There is no silent fallback to the generator model for grading. Missing model
access is an execution error. Token/cost telemetry includes the grading calls.
A malformed faithfulness verdict gets one structural repair attempt; the report
retains the original contract error. A structurally valid adverse quality verdict
is never retried to obtain a better grade. Calibration errors are retained per
case and do not prevent the remaining examples from running.


The ordering turn's internal `reply_language` field defaults to English for older
model outputs and drives localized server corrections. The public chat response
contract is unchanged. Factual questions must preserve the current order; naming
a dish alone is not ordering it. Partial-name matching may resolve a bare category
request, but a category word inside an explicitly named dish cannot authorize an
unrequested sibling dish.

`faithfulness_v4` separates three decisions: question/evidence answerability,
answer-unit classification without evidence, and grounding only the factual units.
Pure limitation statements do not need a passage proving the assistant lacks
knowledge. Mixed statements retain their factual claims for grounding. Refusal
correctness is computed from evidence answerability and the actual response flag;
a second model boolean cannot contradict it. Every stage receives its generated
Pydantic JSON schema and retains its result and any bounded structural repairs.
The 22 approved calibration labels remain unchanged and content-hashed in
`baselines/p0-sample-review.json`.

Exact failed replies can be re-evaluated without regenerating answers:

```bash
uv run python evals/suites/validate_faithfulness.py \
  --source-report path/to/live_eval_results.json \
  --json judge_validation.json --repeats 5
```

This runs the approved calibration, five repetitions of each retained refusal
regression, and all retained RAG replies. It requires the report's actual
`not_found` flags; it never substitutes expected fixture labels. Success requires
every regression and replay case to pass, with unchanged source/assets and a
$0.50 known-cost ceiling (one in-flight request may exceed it). This is judge
validation on retained outputs, not fresh generation or a substitute for CI.
See [the refusal correction evidence](baselines/faithfulness-refusal-fix.md).

## Independent generalization evidence

The [independent review benchmark tooling](benchmarks/README.md) supports collection
audits, independent annotations, lexical/template overlap detection, frozen
validation/test protocols, ONNX-versus-LLM comparison and group-bootstrap uncertainty.
No independent dataset has been supplied yet; this is tooling readiness, not a new
quality claim. Existing synthetic results and blocking regression gates are unchanged.

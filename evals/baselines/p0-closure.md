# P0 closure record — 2026-10-18

The retained judge false rejections below are addressed by the later
[refusal-judge correction](faithfulness-refusal-fix.md). Historical reports remain unchanged.

The owner approved the original ten calibration examples, then explicitly approved
all twelve expanded labels and the unchanged RAG/tone thresholds in this chat.
[The review record](p0-sample-review.json) identifies the reviewer, statements,
case IDs, original dataset hashes and approved content hashes. This is owner
adjudication of authored regression examples, not a blind independent holdout study.

## Acceptance map

| P0 work | Evidence / status |
|---|---|
| Correct AI capability claims | README, architecture, feature matrix, resume/demo/blog docs updated; actual implementations and limitations named |
| Common evaluation contract, isolation, provenance and budgets | `_results.py`, `eval_telemetry.py`, isolated transaction fixtures, pinned seed; regression tests cover errors, unsafe databases, timeout and budget exhaustion |
| Retrieval | 23 live cases; hit@4 and reciprocal rank; blocking at 0.80 |
| Grounded RAG judge | 12 live cases; exact evidence, complete answer-span coverage, bounded structural repair, independent configured grading model; blocking at 0.80 |
| Tone | 21 reused ordering replies; multilingual/refusal rubric; blocking at mean 4/5 |
| Support / inventory / SQL copilot | Live workflow fixtures and deterministic action/quantity/result oracles; blocking at 0.90; safety violations always block |
| Reporting | Shared/API schema, additive migration, admin per-suite reports and provenance; previous migration round trip and frontend build passed |
| CI routing and gates | All seven configured gates blocking; relevant changes route suites, detector failures block, scheduled/manual full runs, retained artifacts |
| Calibration / runbook | 22 owner-approved samples, threshold decision below, negative controls, retained baselines, operation instructions |

## Threshold decision and limits

Thresholds were not lowered to produce green. The owner approved both judge
thresholds after reviewing their exact consequences on 2026-10-18.

| Suite | Threshold | Effective acceptance with current dataset | Tradeoff |
|---|---:|---|---|
| order | 0.95 | At least 167/175 correct, plus existing tool/safety/language gates | Aggregate ordering accuracy can hide individual misses; all are retained |
| retrieval | 0.80 | At least 19/23 document hits | Allows four misses; document hit is a coarse proxy, not passage relevance |
| rag | 0.80 | At least 10/12 passing answers | Allows two quality misses; execution errors always block |
| tone | 0.80 | Mean judge score at least 4/5 | Strong replies can offset a weak reply; no per-language quality floor added |
| support | 0.90 | 6/6 cases | One miss blocks; small sample limits generalization |
| inventory | 0.90 | 5/5 cases | One miss blocks; measures guarded proposal, with fallback/corrections exposed |
| copilot | 0.90 | 6/6 cases | One wrong result blocks even if SQL executes; oracle coverage is finite |

All measured safety bypasses, errors, empty required suites and timeouts block.
No false-pass/false-fail rate on unseen production traffic is claimed. The two
historical final baselines passed all thresholds; threshold-boundary tests now
prove a failure below each deterministic gate actually blocks. Negative controls
cover invented policy with a valid citation, foreign order/refund claims,
excessive PO quantities and executable-but-wrong SQL.

## Expanded calibration found and fixed a defect

The [first expanded check](p0-closure-calibration.json) agreed on 21/22 labels,
with zero execution errors. It incorrectly accepted a refusal despite answerable
evidence. This report is retained, including its then-current review flags.

`faithfulness_v3` adds an explicit evidence-answerability field and truth table.
The scorer rejects an answerability/refusal mismatch even when the judge's
`answerability_correct` boolean is inconsistent. No approved label or threshold
was changed. RAG reports now retain the response's actual `not_found` flag.

The [corrected calibration](p0-closure-calibration-v3.json) agrees on **22/22**
owner-approved labels, with **zero errors**, 22 calls, 17,958 tokens and estimated
cost **$0.019551**. Calibration completion requires both review and agreement;
reviewed samples with execution errors cannot report completed calibration.
All grading calls used `gpt-5.4-mini-2026-03-17`. On this reviewed set, the
faithfulness judge falsely accepted 0/6 invalid answers and falsely rejected
0/5 valid answers; all 11 tone scores were within their approved ranges. These
small-sample results do not estimate production error rates.

## Validation and delivery

The earlier full implementation passed 1,107 Python tests, frontend production
build/TypeScript checks, migration upgrade/downgrade/upgrade and two frozen live
baselines. See [the implementation validation](p0-fix-validation.md).
The final full Python run passed **1,115 tests**, zero failures and zero skips
(298.22 seconds). Ruff lint/format, shell syntax, workflow YAML parsing and
whitespace checks passed. An earlier attempt started before the temporary test
database existed and skipped database tests; it was stopped and is not counted
as completion evidence. Fresh frozen live validation is recorded below.

GitHub was inspected on 2026-10-18: the Live evals workflow is active; the exact
`Live eval gate (order_accuracy >= 0.95)` check is already required on `main`, along
with Python lint/tests, Web build, Eval suites and UI smoke. Administrator bypass
is disabled. OpenAI and scoreboard secret names are configured; this inspection
does not verify secret values. The existing check name is preserved.

## Remaining owner review at delivery

Calibration labels and thresholds are approved; they do not need approval again.
Review the final PR diff and CI results before merging. Merge to `main` deploys
production, so the draft PR will not be merged automatically by this task.
After merge, verify the production migration, admin evaluation report and first
scheduled full run; these cannot be claimed from local validation or an unmerged PR.
Broader blind holdout evaluation and non-P0 multimodal suites remain future work.

## Final activation baselines

Both unchanged-source runs passed **every configured blocking suite threshold**.
Each measured 248 suite cases, with tone reusing 21 ordering replies.

| Suite | Run 1 | Run 2 |
|---|---:|---:|
| order | 96.00% | 96.57% |
| retrieval | 100.00% | 100.00% |
| rag | 100.00% | 91.67% |
| support | 100.00% | 100.00% |
| inventory | 100.00% | 100.00% |
| copilot | 100.00% | 100.00% |
| tone | 85.71% | 82.86% |

Zero execution errors and measured safety bypasses in both runs. Same source
digest, asset hashes and actual database snapshot; cache disabled; pinned fixture
recipe. PR profile uses the complete datasets, with a $0.50 known-cost cap per run.

[Run 1](p0-closure-frozen-1.json) used 460 provider attempts / 1,260,589 tokens /
$0.14859089 estimated cost. [Run 2](p0-closure-frozen-2.json) used 459 attempts /
1,248,996 tokens / $0.13284419. Costs exclude ingestion/setup.

The second run retained one **judge false rejection** (`rag:ans-006`): the
answer correctly refused to invent a coupon code. The judge reason correctly
said the evidence lacked coupon information, but its structured
`evidence_answers_question` field was true, so the scorer rejected the mismatch.
The report retains the first invalid verdict, bounded structural repair and final
quality failure. This falls within the approved 0.80 RAG threshold; it is not
erased or reported as a perfect result. Ordering misses and lower tone scores
remain visible as well. Review this residual judge disagreement in the PR.

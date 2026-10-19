# Synthetic portfolio execution evidence

These are actual runs on the 12 committed artificial examples, not independent
customer-data benchmarks. Their references were proposed during implementation;
no independent human adjudication is claimed. Small percentages are descriptive.

| Component | Run | Result |
|---|---|---|
| INT8 review classifier | `local.json` | Mean per-case label F1 0.444; 0/6 exact matches |
| Review LLM (`gpt-4o-mini`) | `live.json` | Mean per-case label F1 0.889; 4/6 exact matches |
| Synthetic speech (`whisper-1`) | `live.json` | Mean per-case WER 0.111; command-term recall 0.889 on 3 clips |
| Rendered invoice images (`gpt-4o-mini`) | `live.json` | 3/3 exact extractions; deliberately inconsistent bill fails arithmetic verification |
| Forecast candidate | `forecast.json` | Retained incumbent: candidate improves over incumbent but fails the aggregate lag-7 comparison |

The local review weaknesses and the two LLM reference mismatches remain visible.
No thresholds were loosened or labels changed to turn them into passes. The audio
term metric is not actual downstream order-agent success. Invoice variants share
one source template, so there is no meaningful invoice confidence interval.

`initial-provider-failure.json` preserves the first live run: review/vision calls
completed, while all Groq audio calls failed before provider execution because the
local key was a non-ASCII placeholder. The user explicitly authorized using the
existing OpenAI key with Whisper for the portfolio demo. `live.json` records that
override in model provenance. The production Groq default is unchanged. The initial
report's fingerprint belongs to the earlier runner before artifact fingerprinting
was extended; it is retained as failure history, not final-source verification.

Final text/vision known cost: **$0.0085401**, excluding unmeasured STT and tracing /
hosting cost. Initial failed-audio run text/vision cost: **$0.0143577**. An intermediate
successful three-clip audio-only check is also recorded under ignored local
`data/independent/portfolio-results/`; audio costs are unavailable, not zero.

The forecast report uses three recursive 14-day validation folds and a final
report-only 14-day window. These models were trained on artificial data for the
demo. They are not deployed champions. Promotion acceptance and rollback/corruption
paths are also tested with a real loadable artifact in
`packages/ml/tests/test_forecast_lifecycle.py`. No model, artifact or quality gate
was changed to improve these recorded benchmark scores.

## Expanded serving-path assessment — 2026-10-19

`expanded-live.json` is one bounded run on 24 synthetic fixtures, including the
original examples. `expanded-local-confidence.json` audits the actual local policy;
its earlier fingerprint predates the assessment entrypoint, without a model or
threshold change. The live fingerprint covers the expanded runner and serving code at execution.
The subsequent stream cleanup puts creation and consumption under one timeout;
provider SDK retries were also explicitly disabled alongside wrapper retries.
This successful non-streaming demo was not repeated for those defensive changes.
The original reports above remain historical evidence and were not overwritten.

| Path | Result | Remaining errors / limits |
|---|---|---|
| Local classifier confidence audit | 9/12 accepted under legacy policy; all 9 incomplete/incorrect | No fitting; LLM route is now default |
| Actual review serving route | Mean case label F1 0.944; 10/12 exact | `review-0`, `review-4` omit proposed positive aspects |
| STT → real order graph | 5/6 exact drafts; mean WER 0.323 | `audio-4`: English TTS romanized Tanglish → bad transcript → wrong dosa and quantity |
| OCR → actual PO matcher/review decision | 6/6 exact extraction; 6/6 review decisions | Four artificial source groups; human approval always required |
| Concurrent allowance admissions | 5 admitted / 35 rejected at concurrency 8; $0.10 retained | Local DB stress only, zero paid calls; `allowance-stress.json` |
| Synthetic database restore | Counts equal; migration up/down/up passed | No production data; `synthetic-restore-drill.json` |

Known live cost **$0.0301601**, including 124 knowledge chunks embedded. Six short
Whisper calls have unknown audio cost. Tracing/hosting are excluded. There were 32
text/vision/embedding attempts and 218,984 reported tokens; the final vision request
overshot the 200,000 pre-request token admission limit. The $1 known-cost ceiling
was never approached. Per-case latency is recorded but is not a concurrency or VPS
benchmark. Redis cache invalidation logged connection failures on the local machine;
semantic caching was disabled, and all 24 predictions completed.

References remain proposed synthetic labels. Do not call these independent data or
relabel the three mismatches to make the demo green. The failed speech is a reason
to collect permissioned native recordings and test clarification behavior, not proof
that production Tamil speech works or fails at this rate. See [case study](../../../docs/17-ai-engineering-case-study.md).

Local validation: 961 workspace tests plus three backup-publication tests passed;
232 deterministic eval tests and the Next.js production build passed. The final
provider-timeout changes received focused regression checks. The backup shell
tests verify private file modes and preservation of older backups after dump or
verification failure; they stub the CLI and are separate from the real synthetic
PostgreSQL restore drill. PR CI is the authoritative check of the final commit.

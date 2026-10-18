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

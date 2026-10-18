# Portfolio evidence and reliability demonstrations

This project is a portfolio, so the reproducible demonstrations use **synthetic
inputs with explicit provenance**. They show engineering behavior, failure handling
and measurement. They do not establish customer satisfaction, real-world accuracy,
business uplift or independence from model training data. No independent dataset
or human annotation has been invented.

## Run the demonstrations

From the repository root, with the existing workspace dependencies installed:

```bash
# Real local INT8 review predictions; audio and image cases are explicitly not run.
uv run python -m evals.portfolio run --mode local --out /tmp/portfolio-local.json

# Deterministic scorer replay. This is NOT a model accuracy result.
uv run python -m evals.portfolio run --mode replay --out /tmp/portfolio-replay.json

# Actual configured review, Groq STT and vision components. Requires provider keys.
# Generated fixture content may also be traced by the configured Langfuse service.
uv run python -m evals.portfolio run --mode live --out /tmp/portfolio-live.json

# Actual XGBoost training, recursive backtests, selection and immutable releases.
# Training remains on the developer machine / CI, never the 4 GB production VPS.
uv run --all-extras python -m dosadash_ml.forecasting.lifecycle demo --store /tmp/forecast-demo
uv run --all-extras python -m dosadash_ml.forecasting.lifecycle inspect --store /tmp/forecast-demo
# Available only after a promoted release has a predecessor:
uv run --all-extras python -m dosadash_ml.forecasting.lifecycle rollback \
  --store /tmp/forecast-demo --reason 'Candidate regressed during a reviewed canary'
```

Use fresh output paths; reports are never overwritten. Demo generation is optional:
`python -m evals.portfolio generate --out /tmp/new-fixtures` requires Pillow and
ffmpeg with libflite. Committed fixture assets let CI run without those system
packages. TTS voice is libflite `slt`; invoice images are programmatically rendered
and marked synthetic. Challenge cases include negation, rating/text conflict,
Hinglish/Tanglish reviews, speech corrections, synthetic noise, rotation, and an
invoice with inconsistent arithmetic. References are generated proposals, not
independently human-adjudicated truth. Inspect them before using them as a rubric.

Reports include per-case predictions/errors, model identity, latency, language and
challenge slices, source-group counts and descriptive bootstrap intervals. Invoice
variants share one source group. Small intervals are not a quality gate. Reading an
incorrect bill accurately and detecting its arithmetic error are distinct outcomes.
Audio reports WER and command-term recall; the latter is a transcript proxy, not
order-execution accuracy. Provider failures make aggregate quality unavailable.

Review/vision calls use the existing LiteLLM routing and accounting, capped at $1
known cost and 200,000 tokens (one request may overshoot). STT cost is unavailable,
not zero: the included live demo makes three short transcription requests. Calls
have 120-second per-case timeouts. Inputs have content hashes and bounded sizes;
reports fingerprint code/config and detect modifications during execution. Phone
redaction follows the production path. Real fixtures require explicit provenance,
privacy review and provider-use permission. This runner labels them regression
fixtures, never independently validated evidence. Keep real inputs/reports under
ignored `data/independent/`; the stricter independent review workflow remains in
[`evals/benchmarks/README.md`](../evals/benchmarks/README.md).

## Inventory recovery and human approval

The existing relational purchase-order state machine is the execution boundary.
`inventory_workflows` adds a durable generation checkpoint, not a LangGraph
checkpointer or a new service:

1. A request claims a 660-second lease and records an attempt before model work.
2. The validated response commits as `READY` before purchase-order creation.
3. Purchase orders and the workflow's PO IDs commit in the same transaction.
4. `AWAITING_APPROVAL` and `AWAITING_RECEIPT` derive from persisted PO states.
5. Rejected/cancelled/received POs finish the workflow. No automatic approval.

The admin Inventory tab shows recent runs and permits recovery. `draft-now` accepts
`Idempotency-Key`; the web client preserves it after a failed request. Replaying the
same key returns its original POs; changing coverage with that key is rejected.
The nightly key includes the restaurant-local date and coverage window. A recovery
beat runs every 15 minutes, with at most three model attempts before `FAILED`.
Expired leases can be reclaimed; a replaced lease cannot commit a stale result.
Crashing before the model checkpoint can repeat inference. Committed POs are not
repeated. Global transaction locking prevents concurrent supplier drafts, including
unassigned suppliers. PO row locks and ordered ingredient locks prevent concurrent
receipt from incrementing stock twice. Repeated receipt returns a conflict.

Owner Telegram notifications and Redis invalidation remain best-effort; a crash
may miss a notification. The admin queue is the durable approval surface. This is
not an exactly-once message delivery guarantee. Recovery does not send orders to
suppliers or make payments. Human edits, approvals and goods receipt retain RBAC.

Admin endpoints:

- `GET /api/v1/admin/purchase-orders/workflows`
- `POST /api/v1/admin/purchase-orders/workflows/resume?key=...`
- Existing draft/edit/approve/reject/receive endpoints remain supported.

## Forecast health and offline lifecycle

Nightly inference records immutable first-per-issue-day observations alongside the
existing current forecasts. Replays do not overwrite them. Monitoring selects the
latest observation published **before** each target day, compares two 14-day
windows, and requires all menu items on all 28 days and a single model version.
Missing coverage, mixed versions and zero actual demand yield `insufficient_data`.
Observations older than 90 target days are pruned. Historical current forecasts
are not backfilled as if they were valid monitoring evidence.

Degradation requires recent WAPE to exceed all of: 30%, 1.25 times baseline WAPE,
and baseline plus five percentage points. These are transparent portfolio defaults,
not thresholds calibrated on independent restaurant data. The nightly job records
`prepare_candidate_offline` when triggered. Admin Reports displays the latest
health status; `GET /api/v1/admin/reports/forecast-health` exposes the last 30 checks.
The signal does not launch training on the VPS or silently deploy new artifacts.

The offline lifecycle demonstrates two fixed XGBoost candidates on deterministic
synthetic daily sales. Both train before the first validation origin. Three
nonoverlapping 14-day validation windows use recursive predictions (future actuals
never enter within-window lags). The candidate must improve aggregate WAPE by more
than 2%, beat recursive lag-7, and avoid more than 5% regression on any fold. Selection
precedes opening the last 14-day report-only test window. A candidate can correctly
be rejected. The demo is not a hyperparameter search or a real demand benchmark.

Model/evidence releases are immutable directories with verified hashes. Active
version, previous version and audit history change in one atomic JSON replacement
under a file lock. Rollback verifies the predecessor is loadable and unmodified,
then clears the previous pointer to prevent repeated automatic oscillation. The
serving interface can load the verified release directory with `load_champion`.
Production activation remains an artifact PR plus normal CI/deploy review.
Legacy MLflow training now requires matching validation fingerprints and strict
improvement; registry errors cannot masquerade as an empty champion.

## Verification and closure boundary

Regression tests exercise crash-after-generation recovery, process restart,
concurrent draft creation, concurrent stock receipt, bounded retry exhaustion,
RBAC/idempotency, backfill exclusion, immutable observations, future-label leakage,
invalid metric rejection, artifact corruption and rollback with a real loadable
XGBoost artifact. Scorer negative controls drop/duplicate invoice lines and alter
speech quantities/negation. The additive migration is tested up → down → up against
an isolated database. Existing live merge gates remain blocking.

For portfolio scope, no customer dataset is required to demonstrate these paths.
Review the PR, the proposed synthetic references, and the threshold/deployment
tradeoffs before merging. Real-world validation is a separate future evidence
upgrade: collect permissioned independent data, obtain human labels, then run the
frozen independent benchmark. Learned CRM/bandits, online experimentation and
production auto-retraining are not claimed by this work.

Recorded synthetic results and the original provider failure are in
[`evals/portfolio/results/`](../evals/portfolio/results/README.md). The audio demo
uses a user-approved `AI_STT_MODEL=whisper-1` override because the local Groq key was
a placeholder; production still defaults to Groq. To reproduce that run, configure
the OpenAI key and set the override only for the CLI invocation. Use `--tasks audio`
to evaluate only those clips without repeating successful text/vision cases.

# AI engineering case study: measuring the serving path

DosaDash is a portfolio application on a 4 GB VPS. The useful engineering question
is whether the measured model behavior matches what the application actually
serves, and whether failures stay inside explicit business and operational limits.
All examples below are synthetic; independent customer-data accuracy is unmeasured.

## A strong training metric hid confident mistakes

The historical LoRA/INT8 review artifact achieved macro-F1 0.9944 on generated
holdout reviews. A separate six-example challenge set exposed five confident but
incorrect or incomplete predictions. The old hybrid route accepted those five;
only the abstention reached the LLM. Comparing an LLM-only benchmark with that
local-first serving policy overstated the application quality.

The expanded audit contains the six exposed examples plus six new authored
challenges. Nine of twelve local outputs would be accepted by the old route;
all nine miss at least one proposed label or add an incorrect label. These are
small diagnostic samples, not an independently labeled test set. No threshold was
fitted and no artifact was retrained on them. Review scoring now defaults to the
existing LLM/batch route. Local-first scoring remains an explicit experiment flag.
The tradeoff is additional provider cost and dependency, controlled by admission
limits rather than a claim that the LLM is always right.

The live serving route scored **10/12 exact matches**, mean per-example label F1
**0.944**. Two omitted positive-aspect labels remain visible. These metrics are
not comparable to the historical macro-F1 metric on a different dataset.

## Measure the downstream decision

Six clips pass through real Whisper transcription and the real order graph with
an isolated seeded database. **Five produced the expected draft**, including a
correction and a no-sugar request. The romanized Tanglish case, spoken by an English
TTS voice, became “Randum asolidosa…”; the resulting draft contained one Rava Dosa
instead of two Masala Dosas. This demonstrates a failure, not native Tamil coverage.
The human sees the transcript/draft before checkout; no evaluation order is placed.
Mean per-clip WER is **0.323**. Draft correctness is more useful than transcript
keyword recall alone, but six artificial clips do not establish production safety.

Six rendered invoices span four vendor/layout groups and pass through extraction,
arithmetic verification, actual PO matching and the shared review-status function.
All six extractions and review decisions matched the proposed references. One bill
has incorrect arithmetic, one a short delivery, and one an extra fee. Previously,
weighted confidence could hide a known discrepancy and mark it MATCHED. The decision
now requires correct arithmetic, acceptable quantities, no extra lines and the
existing confidence threshold. MATCHED still requires human approval before stock
changes. The arithmetic metric is 5/6 by design: correctly reading a bad bill must
not make its arithmetic valid.

## Bound failure and make the evidence inspectable

A PostgreSQL ledger reserves estimated USD and call attempts before provider I/O.
Atomic updates share the allowance across workers and process restarts. Known costs
settle once; unknown charges, timeouts and crashes retain their reservation. The
ledger fails closed when unavailable. Retries and fallbacks each reserve allowance;
provider calls have a wall-clock bound and streamed responses are closed on failure.
This does not guarantee a provider billing cap: actual charges can exceed estimates.

A local stress check submitted 40 admissions at concurrency eight against a $0.10
allowance: **five admitted, 35 rejected, $0.10 retained**, with zero provider calls.
Its p95 was 161 ms on the developer machine; it is not VPS chat throughput. Mocked
outage tests cover fallback, hanging calls, stream cleanup and rejection across
chat, embeddings, audio, images and batch submission.

The live demo used **$0.0301601 known cost**, including knowledge embeddings, across
32 text/vision/embedding attempts. Six audio calls have unavailable cost; tracing,
hosting and local compute are excluded. The $1 admission check was never reached.
The final vision call took reported tokens to 218,984, past the 200,000 pre-request
limit; a single admitted request can overshoot. Audio used an approved Whisper
`whisper-1` override; production remains configured for Groq STT.

## Recovery and honest scope

A local synthetic database with 60 menu items, 40 generated users and 3,075 generated
orders was dumped, restored and migrated up/down/up. Aggregate counts stayed equal;
the temporary archive and restored database were removed. That is a reproducible
mechanism check, not proof of recovery from the live production database.

The operational audit found no installed DosaDash backup cron or systemd timer.
The earlier nightly-backup claim has been corrected. A permission-restricted backup
script and schedule example are supplied for deployment review. Off-host storage,
failure alert delivery and a production-data restore drill remain open operational
work; the production transfer requires explicit permission.

Inventory recovery uses relational checkpoints and leases, not durable LangGraph
order sessions. Forecast health needs 28 valid observation days and can correctly
report insufficient data. Offline promotion/rollback is demonstrated; production
training remains off the VPS and artifact activation stays PR-based. Learned CRM,
bandits, real online experiments and distributed training are outside this portfolio.

## Evidence and reproduction

- [Dated results, failures and raw reports](../evals/portfolio/results/README.md)
- [Benchmark and workflow demonstrations](16-portfolio-evidence.md)
- [Allowance and recovery operations](18-ai-operations.md)
- [Independent dataset contract](../evals/benchmarks/README.md)

A defensible résumé claim is: “Built an eval-gated FastAPI/LangGraph application
with structured business guardrails, synthetic multimodal downstream benchmarks,
durable workflow recovery and shared provider admission limits on a 4 GB VPS.”
Avoid claiming independent real-world accuracy, zero-cost inference, fully autonomous
operations or production recovery certification from these demonstrations.

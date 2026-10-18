# Independent review benchmarks

This tooling is ready for data collection. **No independent dataset has been
provided, no human annotations have been invented, and no generalization result
is claimed.** Existing synthetic/regression scores stay in their original files.

The first supported task is review aspect sentiment: the actual INT8 ONNX
classifier versus the production review LLM prompt and sanitization path. This
is a comparison of the two components, not a measurement of the hybrid serving
router. The LLM receives text and rating as in production; ONNX receives text only. It runs locally without business-database access, new services or training.

## Collect and annotate

Keep inputs and reports under the ignored `data/independent/` directory. Do not
commit raw customer data, permission records, invoices, audio or reviewer identities.
Use pseudonymous record, author, reviewer and source-group IDs. Document permission
and retention outside the public repository. Provider permission includes the
configured LLM provider chain and any configured tracing service; a local-only
permission does not authorize external model evaluation.

For the first collection, aim for 240 review texts: 120 validation, 120 final test.
Include English, Hinglish and Tanglish, plus a clearly reported native-script
challenge slice if collected. Include negation, mixed aspects, rating/text conflict,
code-switching, spelling errors, indirect complaints and unfamiliar phrasing. Do
not compose examples by paraphrasing the existing synthetic templates. Ask authors
and labelers to work without seeing model outputs or training templates.

The benchmark currently requires at least one aspect label. Rating-only, truly
aspectless and neutral reviews need a separately specified task; do not force a
positive/negative label onto them to fit this contract. Both polarities for an
aspect are allowed when the text actually expresses both; label the evidence.

Each JSONL record conforms to `contracts.ReviewRecord`:

- `id`, `group_id`, `split` (`val` or `test`), `text`, `rating`, `language`, `categories`;
- `origin`: `permissioned_real` or `human_authored`; synthetic records are rejected;
- `source_ref`, `permission_ref`, `author`: pseudonymous, locally resolvable references;
- `pii_reviewed`, `independent_of_training`, `provider_use_allowed`: explicit attestations;
- `annotations`: at least two distinct human reviewers, neither the text's author;
  each supplies `method: human`, `reviewer` and a list of registered aspect/polarity
  labels such as `taste:POSITIVE` or `delivery:NEGATIVE`;
- `adjudication`: required when annotators disagree; a third independent reviewer
  supplies the resolved labels and a reason of at least ten characters.

Label explicit aspect evidence, not the star rating. Cover taste, portion,
packaging, delivery, temperature, freshness, spice and value using the registry
in `packages/shared/src/dosadash_shared/reviews.py`. Reviewers should annotate
separately before comparing labels. Record unresolved ambiguity; do not run those
records until adjudicated. The audit reports initial exact-label disagreement even
when adjudicated. Agreement does not establish label validity by itself.

Group records by shared source/customer/session, invoice, or authoring scenario as
appropriate before splitting. A group may belong to only one split. Split and
freeze before model selection; tune only on validation. The default protocol
requires 30 source groups in **each** split. This is an operational floor, not a
statistical power calculation. Report small language/category slices honestly.

Generate the input schema locally if useful:

```bash
mkdir -p data/independent/reviews-v1
uv run python -c 'import json; from evals.benchmarks.contracts import ReviewRecord; print(json.dumps(ReviewRecord.model_json_schema(), indent=2))' > data/independent/reviews-v1/schema.json
```

## Audit and freeze

The audit checks all known default synthetic splits, existing review regression
inputs, normalized duplicates, near duplicates (token Jaccard ≥0.8 for at least
four tokens), and spans matching the known synthetic templates. It checks overlap
within the independent dataset too. Add **all** additional training/tuning inputs
as JSONL rows with a `text` field using `--references`; keep this file private.
Automatic checks cannot establish independence from unobserved data, semantic
paraphrases or foundation-model pretraining. Human review remains necessary.

```bash
uv run python -m evals.benchmarks audit \
  --records data/independent/reviews-v1/records.jsonl \
  --out data/independent/reviews-v1/audit.json

uv run python -m evals.benchmarks freeze \
  --records data/independent/reviews-v1/records.jsonl \
  --dataset-id independent-reviews --version v1 --backend onnx \
  --out data/independent/reviews-v1/frozen.json
```

Use `--backend compare` to compare ONNX with the configured LLM chain, or `llm`
for that component alone, only with the required provider permissions. No labels,
source references, categories or adjudication rationale are sent to the model.
Phone redaction uses the existing production function. Human PII review must cover
other identifiers. ONNX has no provider call and does not download model weights.

The manifest fingerprints records, overlap references, model artifacts, prompt,
routing chain, dependencies, scoring code and protocol. Any change invalidates it.
A new validation candidate needs a new manifest; existing reports are never
replaced. Dataset provenance and frozen hashes are consistency checks, not signatures
that can independently authenticate a person's attestations.

## Validate, then release final test

```bash
uv run python -m evals.benchmarks run \
  --records data/independent/reviews-v1/records.jsonl \
  --manifest data/independent/reviews-v1/frozen.json \
  --out data/independent/reviews-v1/validation-01.json

# Only after candidate/model/prompt selection is final:
uv run python -m evals.benchmarks run \
  --records data/independent/reviews-v1/records.jsonl \
  --manifest data/independent/reviews-v1/frozen.json --split test --release-test \
  --out data/independent/reviews-v1/final-test.json
```

Pass the same `--references` and `--model-dir` overrides used at freeze. Dataset
version is an immutable collection version, not an experiment counter. Do not
rename it, relocate the ledger or alter test labels to reuse a revealed holdout.

Test access is reserved atomically under `data/independent/test-access/` before
execution. Repeated or concurrent final-test access for a dataset/version fails,
including after an interrupted or failed provider run. Preserve the ledger with
the collection when moving machines. It guards accidental reuse in this checkout;
repository owners can still inspect files or delete the ledger. For stronger blind
assessment, let an independent custodian hold the final test set outside the repo.
Any exceptional rerun must be documented as a reuse, not an untouched test.

Reports include component predictions by ID, failures, language/category metrics,
source-group percentile-bootstrap 95% intervals, and paired ONNX-minus-LLM deltas.
Intervals are unavailable for a single source group and flagged as unstable for
fewer than 30. Category slices overlap; do not sum their counts. No automatic
quality threshold, winner declaration, model promotion or scoreboard publication
is introduced. Execution completion is not a quality pass.

Provider failures, omissions and timeouts remain errors; affected aggregate/slice
metrics are unavailable rather than scored over only successful cases. Partial
predictions are retained. The external-call ceiling is $1 known cost, 1,000
attempts and 2 million tokens; each LLM case has a 120-second timeout. One request
can overshoot a ceiling, and provider-internal retries may not be separately
visible. Unknown cost is null. CPU time, hosting and annotation are not free.

## Sentiment training selection correction

New sentiment training runs select using **validation** macro-F1 and a matching
validation-text/label fingerprint. Strict improvement is required. Legacy champions
whose scores came from the synthetic test split are deliberately incomparable;
they remain deployed and must be rescored on the same validation set before any
promotion. Registry/network errors cannot imply that no champion exists. The
current artifact and historical synthetic report are unchanged. Final synthetic
test metrics are computed only after selection and are reporting-only. This does
not retroactively make the old synthetic test distribution independent.

## Remaining evidence and modalities

To finish the independent-evidence milestone, people must supply permissioned
inputs, independent annotations/adjudication, and review the source/overlap audit.
Then freeze the candidate, run validation and final test, and publish approved
aggregate evidence separately from synthetic results. There are no results yet.

Audio and invoice collections can follow the same provenance/group/annotation
principles, but **their runners are not implemented here**. Audio needs permissioned
recordings, independent verbatim transcripts, language/noise labels and word-error
plus downstream-intent scoring. Invoices need authorized images, independently
transcribed line items/totals, vendor/template groups and extraction/arithmetic
metrics; JSON-only matching fixtures cannot measure image-reading generalization.
Durable inventory execution and forecasting lifecycle automation are separate
subsequent roadmap items, not completed by this benchmark tooling.

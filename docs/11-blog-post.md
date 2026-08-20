# 11 — Blog Post (draft for publication)

> Status: DRAFT — publish to personal blog / dev.to / Medium after the Phase 9
> deploy, with screenshots swapped in at the marked spots. Target length ~2,000
> words; this draft is intentionally complete so publishing is an edit, not a write.

---

# Evals Are Merge Gates: What I Learned Building an AI-Native Cloud Kitchen on 4 GB of RAM

I spent 12 weeks building [DosaDash](https://dosadash.venkateshs.dev) — a South
Indian cloud-kitchen platform where every subsystem is AI-driven: a multilingual
ordering agent, production RAG, forecast-driven inventory agents, a fine-tuned
sentiment model, voice ordering, vision-model invoice OCR, and an MCP server so
Claude Desktop can order a dosa. It runs, in production, on a single 4 GB VPS.

This isn't a post about any one of those features. It's about the four
engineering decisions that made the whole thing hold together — and the numbers
that came out the other side.

## 1. Evals are merge gates, not dashboards

The single highest-leverage decision: a golden set of conversations (now 168,
across English, Hinglish, Tanglish and Tamil script, with 45 adversarial cases)
runs **live against every PR that touches an AI path**, and the merge is blocked
if order-extraction accuracy drops below 95%.

This is not theater. The gate has blocked two real regressions:

- One run came in at **85%** — a data-calibration problem the golden set caught
  immediately.
- Another at **92.7%** — subtle prompt regressions around name fidelity and
  cross-lingual numerals ("రెండు dosa" is two dosas, not one).

Both were fixed *before* merge, with the fixes themselves becoming new eval
cases. The scoreboard keeps the red runs on display — they're the proof the
gate earns its keep. Current standing: **96.4% accuracy, 100% tool correctness,
0 guardrail bypasses**.

[SCREENSHOT: eval scoreboard with the two red runs]

Two practical lessons about live LLM gates:

- **Have a flake protocol.** LLM evals wobble. We keep a documented list of
  known-flaky cases; if a gate fails with only those, the protocol is re-run
  first, debug second. It has been right every time — including a failure on a
  PR that couldn't possibly have changed agent behavior (it added *metrics
  counters*), which passed clean on re-run.
- **Key-free asset gates are the cheap 80%.** Most eval value came from unit-CI
  gates that need no API keys: coverage floors on the golden set, "no
  off-registry label can survive the guardrail", "prompt constants match the
  schema". They run in seconds on every commit.

## 2. Guardrails: the model observes, the code decides

The rule that shaped every agent in the system: **LLMs propose, deterministic
code disposes.**

- The order agent emits a structured `OrderDraft`; every item id is validated
  against the database before checkout. A hallucinated dish *cannot* reach an
  order — not because the prompt asks nicely, but because the code checks.
- The inventory agent doesn't decide purchase quantities. Deterministic math
  (forecast × recipe minus stock) computes needs; the LLM writes supplier-facing
  copy; a guardrail re-anchors any hallucinated ingredient, clamps quantities to
  a band, and force-adds omissions. A human approves every PO.
- The dish-photo QC model only *reports what it sees* ("is this food, which
  dishes, what issues"); the pass/fail verdict is computed. A blurry non-food
  photo can never PASS, even if the model claims it saw a dosa.
- The support agent can check status and cancel un-cooked orders. Refunds are
  structurally impossible for it to execute — they become escalations in a
  human inbox. Not "instructed not to". *Can't.*

Once you adopt this shape, prompt injection mostly stops being scary: the
45-case adversarial suite (including Tamil-script injection) has **zero
bypasses**, because there's nothing for the injected text to seize — the
dangerous verbs live on the other side of a type-checked boundary.

## 3. Measure, don't assume (a series of humblings)

Every time I trusted intuition over measurement, the measurement won:

- **ALS recommender**: raw-count confidences *lost to the popularity baseline*
  (Recall@10 0.363 vs 0.378). log1p scaling flipped it to **0.417** — and more
  importantly, tail-Recall@10 of **0.345 vs a structural 0.000** for
  popularity. On a 52-item menu, the long tail *is* the personalization story.
- **Combo suggester**: my carefully-chosen fixed category priority *lost to
  random* in the simulated A/B (11.8% vs 12.8% attach). Letting the score pick
  the category won (15.6%).
- **Quantization**: everyone "knows" signed INT8. Measured, QUInt8 kept
  macro-F1 at 0.9937 vs QInt8's 0.9875. We ship what measured better.
- **Caching**: I *wanted* to tune the semantic-cache threshold in the hardening
  phase. First shipped counters instead — tuning blind would have repeated the
  ALS mistake.

The discipline generalizes: **every model must beat its naive baseline to be
promoted** (WAPE 0.421 vs 0.555 for lag-7 forecasting; the MLflow champion
alias only moves on measured improvement), and every benchmark artifact is
committed to the repo with its caveats written inside it — the A/B sim says
plainly that it validates taste recovery on synthetic personas, not real-world
uplift.

## 4. Fine-tuning won — but only because the numbers said so

For review aspect-sentiment (8 aspects × 2 polarities), I benchmarked a LoRA
DistilBERT against gpt-4o-mini zero-shot **on the same held-out reviews,
through the same scorer**:

| | LoRA (INT8, on-VPS CPU) | gpt-4o-mini zero-shot |
|---|---|---|
| macro-F1 | **0.9944** | 0.9926 |
| cost / 1k reviews | **₹0** | ₹3.20 |
| latency / review | 57 ms (local) | network round-trip |

The fine-tune wins narrowly on accuracy and absolutely on economics — *for
this narrow, high-volume task*. The serving design is the interesting part: a
deterministic confidence contract routes ~97% of reviews to the local INT8
model and escalates the ambiguous residue to the LLM — which runs nightly
through the provider's Batch API at half price. Honesty note that also lives in
the artifact: this measures planted-label recovery on synthetic reviews, not
human agreement.

## 5. Production on 4 GB is a feature, not a constraint

The RAM budget forced choices that made the system better:

- **No local LLMs** — everything through litellm with a 3-model fallback chain;
  the only local models are the ONNX INT8 classifier (~200 MB peak) and
  numpy-only recsys serving (training deps never enter the images).
- **One Postgres** for OLTP + vectors + full-text search. No drift between the
  business DB and a vector store, one backup, and the event cascade (menu edit →
  re-embed → cache flush) is trivial to keep consistent.
- **Fail-open everywhere it's safe**: rate limiting, caches, event publishing —
  a Redis outage degrades cost, never checkout. (And rate limiting is pure-ASGI
  so the SSE token stream passes through untouched.)

Under load: **100 concurrent users, zero failures, aggregate P50 24 ms / P95
210 ms**, checkout P95 370 ms including payment capture — single-process
uvicorn, same topology as prod. The limiter shed 343 abusive requests in the
stress pass while served latency held at 17 ms P50.

And one honest postmortem: the first Phase 7 deploy crash-looped the api on a
root-owned Docker volume — caught in minutes by the deploy pipeline's own
health-check smoke. The fix became a standing pattern: *nice-to-have mounts
degrade; they never take checkout down.*

## What I'd tell past me

1. Build the eval gate in week 7, not week 12. Everything after it compounds.
2. Write the guardrail before the prompt. The prompt is UX; the guardrail is
   the product.
3. Commit your benchmark artifacts with their caveats inside. Future-you will
   try to round up.
4. Synthetic data with planted signal is a superpower for evals — and a
   permanent asterisk on your metrics. Print the asterisk.

*DosaDash is live at [dosadash.venkateshs.dev](https://dosadash.venkateshs.dev)
(demo credentials on [/demo](https://dosadash.venkateshs.dev/demo)), source at
[github.com/venkathub/dosadash](https://github.com/venkathub/dosadash). Yes,
Claude Desktop can order a dosa. It appears on the kitchen display and
everything.*

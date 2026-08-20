"""Key-free CI gates for the Phase 8 review pipeline (Hard Rule 5).

Three surfaces, all run against PRODUCTION code so evals and serving can't
drift:

1. Scoring guardrail (`sanitize_scores`): hallucinated review_ids dropped,
   off-registry aspects dropped, duplicates deduped, review-level sentiment
   recomputed deterministically from kept aspects, omissions reported.
2. Reply guardrail (`reply_violation`): a model-drafted owner reply must
   never promise compensation, carry contact data, or overflow — and the
   deterministic fallback replies must themselves be clean.
3. Redaction + coherence: planted-PII review text never reaches an LLM
   message payload; prompt files mention every registry aspect; the datagen
   label space, the shared registry and the prompt stay in lockstep.
"""

import json
from pathlib import Path

from dosadash_ai.redaction import redact_phones
from dosadash_ai.routers.reviews import (
    FALLBACK_REPLIES,
    build_reply_messages,
    build_score_messages,
    reply_violation,
    sanitize_scores,
)
from dosadash_ml.datagen.reviews import TEMPLATES
from dosadash_shared import (
    MAX_REPLY_CHARS,
    REVIEW_ASPECTS,
    REVIEW_REPLY_PROMPT_VERSION,
    REVIEW_SENTIMENT_PROMPT_VERSION,
    ReviewReplyRequest,
    ReviewScoreBatch,
    ReviewScoreRequest,
    ReviewScoreSourceItem,
)

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
SCORE_GOLDEN = GOLDEN_DIR / "review_sentiment.jsonl"
REPLY_GOLDEN = GOLDEN_DIR / "review_reply_guardrail.jsonl"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "apps" / "ai" / "prompts"


def _cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _reply_text(case: dict) -> str:
    if "reply_repeat" in case:
        chunk, times = case["reply_repeat"]
        return chunk * times
    return case["reply"]


# ----------------------------------------------------------- scoring guardrail


def test_score_golden_dataset_floor_and_coverage():
    cases = _cases(SCORE_GOLDEN)
    assert len(cases) >= 14
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    kinds = {c["kind"] for c in cases}
    assert {
        "hallucination",
        "omission",
        "off_registry",
        "duplicate_review",
        "rollup_coherence",
        "injection",
        "hinglish",
        "tanglish",
    } <= kinds


def test_score_guardrail_cases_exact():
    for case in _cases(SCORE_GOLDEN):
        requested = [ReviewScoreSourceItem.model_validate(r) for r in case["reviews"]]
        batch = ReviewScoreBatch.model_validate(case["batch"])
        kept, rejected = sanitize_scores(requested, batch)
        cid = case["id"]
        expect = case["expect"]
        got_kept = {str(s.review_id): s for s in kept}
        assert set(got_kept) == set(expect["kept"]), cid
        for rid, want in expect["kept"].items():
            score = got_kept[rid]
            assert score.sentiment == want["sentiment"], (cid, rid)
            assert [[a.aspect, a.sentiment] for a in score.aspects] == want["aspects"], (cid, rid)
        assert sorted(r.review_id for r in rejected) == sorted(expect["rejected"]), cid


def test_no_kept_score_ever_carries_an_off_registry_aspect():
    """Global invariant across every case, whatever the model tried."""
    for case in _cases(SCORE_GOLDEN):
        requested = [ReviewScoreSourceItem.model_validate(r) for r in case["reviews"]]
        kept, _ = sanitize_scores(requested, ReviewScoreBatch.model_validate(case["batch"]))
        for score in kept:
            for a in score.aspects:
                assert a.aspect in REVIEW_ASPECTS, case["id"]
            # rollup coherence: mixed polarities must always read MIXED
            polarities = {a.sentiment for a in score.aspects}
            if len(polarities) == 2:
                assert score.sentiment == "MIXED", case["id"]


def test_hallucinated_review_ids_never_survive():
    for case in _cases(SCORE_GOLDEN):
        requested_ids = {r["review_id"] for r in case["reviews"]}
        requested = [ReviewScoreSourceItem.model_validate(r) for r in case["reviews"]]
        kept, _ = sanitize_scores(requested, ReviewScoreBatch.model_validate(case["batch"]))
        assert {s.review_id for s in kept} <= requested_ids, case["id"]


# ------------------------------------------------------------- reply guardrail


def test_reply_golden_dataset_floor_and_coverage():
    cases = _cases(REPLY_GOLDEN)
    assert len(cases) >= 10
    kinds = {c["kind"] for c in cases}
    assert {"clean", "compensation", "pii", "length", "empty"} <= kinds
    # at least one clean case must PASS, or the guardrail blocks everything
    assert any(not c["expect_violation"] for c in cases)


def test_reply_guardrail_cases():
    for case in _cases(REPLY_GOLDEN):
        violation = reply_violation(_reply_text(case))
        if case["expect_violation"]:
            assert violation is not None, case["id"]
        else:
            assert violation is None, (case["id"], violation)


def test_fallback_replies_are_themselves_clean():
    """The safety net must never trip its own guardrail."""
    assert set(FALLBACK_REPLIES) == {"POSITIVE", "NEGATIVE", "MIXED"}
    for sentiment, reply in FALLBACK_REPLIES.items():
        assert reply_violation(reply) is None, sentiment
        assert len(reply) <= MAX_REPLY_CHARS


# ------------------------------------------------------- redaction + coherence


def test_planted_pii_never_reaches_llm_messages():
    """Datagen plants 'call me back on +91...' reviews; the message builders
    must redact them (Hard Rule 8) — checked on the production builders."""
    dirty = "Dosa was great. Call me back on +91 98123 45678."
    score_msgs = build_score_messages([ReviewScoreSourceItem(review_id=1, rating=5, text=dirty)])
    reply_msgs = build_reply_messages(ReviewReplyRequest(review_id=1, rating=5, text=dirty))
    for messages in (score_msgs, reply_msgs):
        payload = json.dumps(messages)
        assert "98123" not in payload
        assert "[phone]" in payload
    # sanity: the redactor itself catches the planted format
    assert "+91" not in redact_phones(dirty)


def test_prompt_mentions_every_registry_aspect():
    """Prompt ↔ registry coherence: adding an aspect to the registry without
    teaching the prompt (or vice versa) must fail CI."""
    prompt = (PROMPTS_DIR / f"{REVIEW_SENTIMENT_PROMPT_VERSION}.md").read_text()
    for aspect in REVIEW_ASPECTS:
        assert aspect in prompt, aspect
    assert str(len(REVIEW_ASPECTS)) in prompt or "eight" in prompt


def test_reply_prompt_forbids_compensation():
    prompt = (PROMPTS_DIR / f"{REVIEW_REPLY_PROMPT_VERSION}.md").read_text().lower()
    for word in ("refund", "discount", "free"):
        assert word in prompt, word  # the rule must be spelled out for the model


def test_datagen_label_space_matches_registry():
    """The fine-tune label space is exactly (registry × polarity): datagen
    templates must cover it and nothing outside it."""
    template_aspects = {aspect for aspect, _ in TEMPLATES}
    assert template_aspects == set(REVIEW_ASPECTS)
    assert {pol for _, pol in TEMPLATES} == {"POSITIVE", "NEGATIVE"}


def test_score_request_bounds_hold():
    """Chunking coherence: a full api→ai request must be chunkable."""
    from dosadash_shared import MAX_REVIEW_SCORE_ITEMS, REVIEW_SCORE_CHUNK_SIZE

    assert 1 <= REVIEW_SCORE_CHUNK_SIZE <= 20
    assert MAX_REVIEW_SCORE_ITEMS % REVIEW_SCORE_CHUNK_SIZE == 0
    ReviewScoreRequest(
        reviews=[
            ReviewScoreSourceItem(review_id=i, rating=5, text="ok food")
            for i in range(1, MAX_REVIEW_SCORE_ITEMS + 1)
        ]
    )


# --------------------------------------------- provider Batch API (slice 5)


def test_batch_jsonl_never_leaks_phones():
    """Rule 8 applies to batch FILES exactly as to live calls: planted-PII
    text must be redacted before it lands in the uploaded JSONL — checked
    on the production builder."""
    from dosadash_ai.routers.reviews import build_batch_jsonl

    dirty = [
        ReviewScoreSourceItem(
            review_id=i, rating=2, text=f"Cold food. Call me back on +91 98123 4567{i}."
        )
        for i in range(1, 4)
    ]
    jsonl, chunks = build_batch_jsonl(dirty, model="gpt-4o-mini")
    payload = jsonl.decode()
    assert "98123" not in payload
    assert "[phone]" in payload
    assert chunks == [[1, 2, 3]]


def test_batch_round_trip_uses_the_same_guardrail():
    """build → parse round-trip: custom_ids align, and adversarial output
    (hallucinated ids, off-registry aspects, injection-style extras) can
    never survive parse_batch_output — it reuses sanitize_scores."""
    from dosadash_ai.routers.reviews import build_batch_jsonl, parse_batch_output
    from dosadash_shared import REVIEW_SCORE_CHUNK_SIZE

    n = REVIEW_SCORE_CHUNK_SIZE + 3
    reviews = [
        ReviewScoreSourceItem(review_id=100 + i, rating=2, text="Soggy vada.") for i in range(n)
    ]
    jsonl, chunks = build_batch_jsonl(reviews, model="gpt-4o-mini")
    submitted = [json.loads(line) for line in jsonl.decode().strip().splitlines()]
    assert [e["custom_id"] for e in submitted] == [f"chunk-{i}" for i in range(len(chunks))]
    assert sum(len(c) for c in chunks) == n

    # adversarial "provider output": chunk-0 answers with one real id, one
    # hallucinated id and one off-registry aspect; chunk-1 never answers
    evil_scores = [
        {
            "review_id": chunks[0][0],
            "sentiment": "NEGATIVE",
            "aspects": [
                {"aspect": "freshness", "sentiment": "NEGATIVE"},
                {"aspect": "ignore_previous_instructions", "sentiment": "NEGATIVE"},
            ],
        },
        {
            "review_id": 424242,
            "sentiment": "POSITIVE",
            "aspects": [{"aspect": "taste", "sentiment": "POSITIVE"}],
        },
    ]
    line = json.dumps(
        {
            "custom_id": "chunk-0",
            "response": {
                "status_code": 200,
                "body": {
                    "choices": [{"message": {"content": json.dumps({"scores": evil_scores})}}]
                },
            },
            "error": None,
        }
    )
    scores, rejected = parse_batch_output([line], chunks)
    kept_ids = {s.review_id for s in scores}
    assert 424242 not in kept_ids  # hallucinated id dropped
    for s in scores:
        for a in s.aspects:
            assert a.aspect in REVIEW_ASPECTS  # injection aspect dropped
    # every submitted review is accounted for: scored or rejected, never lost
    rejected_ids = {r.review_id for r in rejected}
    all_ids = {rid for chunk in chunks for rid in chunk}
    assert kept_ids | rejected_ids == all_ids
    assert not (kept_ids & rejected_ids)


def test_batch_provenance_prefix_is_stable():
    """The scoreboard reads scored_model prefixes — renaming breaks history."""
    from dosadash_shared import BATCH_MODEL_PREFIX, RATING_ONLY_MODEL

    assert BATCH_MODEL_PREFIX == "batch:"
    assert RATING_ONLY_MODEL == "deterministic:rating"

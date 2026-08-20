"""Review scoring + reply chain tests (Phase 8): endpoint auth, rating-only
split, local INT8-champion-first scoring (slice 4), redaction-before-LLM,
guardrail end-to-end, and the reply fallback — litellm is always mocked and
the local champion is stubbed (CI never calls providers nor loads ONNX here;
artifact parity gates live in evals/suites/test_sentiment_serving_assets.py).
Pure sanitizer cases live in evals/suites/test_review_assets.py."""

import json

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.llm import client as llm_client
from dosadash_ai.routers import reviews as reviews_router
from dosadash_ml.finetune.predict import SentimentPrediction
from dosadash_shared import (
    REVIEW_SCORE_CHUNK_SIZE,
    ReviewReplyRequest,
    ReviewScoreRequest,
    ReviewScoreSourceItem,
)

SCORE = "/internal/reviews/score"
REPLY = "/internal/reviews/draft-reply"
TOKEN = {"X-Internal-Token": "test-internal-token"}


class FakeResponse:
    def __init__(self, content: str) -> None:
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message})()]


def _echo_scores(kwargs, extra: list[dict] | None = None) -> FakeResponse:
    """Tag every review the user message carried as NEGATIVE-delivery."""
    payload = json.loads(kwargs["messages"][-1]["content"])
    scores = [
        {
            "review_id": r["review_id"],
            "sentiment": "NEGATIVE",
            "aspects": [{"aspect": "delivery", "sentiment": "NEGATIVE"}],
        }
        for r in payload["reviews"]
    ]
    return FakeResponse(json.dumps({"scores": scores + (extra or [])}))


def _score_req(n: int = 2, text: str = "Late delivery.") -> ReviewScoreRequest:
    return ReviewScoreRequest(
        reviews=[ReviewScoreSourceItem(review_id=i, rating=2, text=text) for i in range(1, n + 1)]
    )


def _reply_req(**overrides) -> ReviewReplyRequest:
    base = {"review_id": 1, "rating": 2, "text": "Sambar leaked everywhere."}
    return ReviewReplyRequest(**{**base, **overrides})


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_local_champion(monkeypatch):
    """LLM-path tests run without the local champion (as if the artifact
    were absent — the degrade path). Local-path tests override this."""
    monkeypatch.setattr(reviews_router, "local_champion", lambda: None)


class FakeChampion:
    """Stub SentimentChampion: canned per-text predictions."""

    version = "dosadash-sentiment/v2-int8"

    def __init__(self, by_text: dict[str, SentimentPrediction]) -> None:
        self.by_text = by_text
        self.seen: list[str] = []


def _fake_predict(champion: FakeChampion, texts: list[str]) -> list[SentimentPrediction]:
    champion.seen.extend(texts)
    return [champion.by_text.get(t, SentimentPrediction(labels=(), confident=False)) for t in texts]


@pytest.fixture
async def ai_client():
    from dosadash_ai.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ------------------------------------------------------------------- scoring


async def test_score_requires_internal_token(ai_client):
    body = _score_req().model_dump(mode="json")
    assert (await ai_client.post(SCORE, json=body)).status_code == 403


async def test_score_end_to_end_with_guardrail(ai_client, monkeypatch):
    """Real scores survive; a hallucinated review_id and an off-registry
    aspect from the same output are dropped by the guardrail."""

    async def fake_acompletion(**kwargs):
        ghost = {
            "review_id": 999,
            "sentiment": "NEGATIVE",
            "aspects": [{"aspect": "delivery", "sentiment": "NEGATIVE"}],
        }
        return _echo_scores(kwargs, extra=[ghost])

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(SCORE, json=_score_req().model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["review_id"] for s in body["scores"]] == [1, 2]
    assert body["model"] == "gpt-4o-mini"
    assert body["prompt_version"] == "review_sentiment_v1"


async def test_rating_only_reviews_never_reach_the_llm(ai_client, monkeypatch):
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(json.loads(kwargs["messages"][-1]["content"]))
        return _echo_scores(kwargs)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    req = ReviewScoreRequest(
        reviews=[
            ReviewScoreSourceItem(review_id=1, rating=5, text=""),
            ReviewScoreSourceItem(review_id=2, rating=1, text="   "),
            ReviewScoreSourceItem(review_id=3, rating=3, text="Okay-ish dosa."),
        ]
    )
    resp = await ai_client.post(SCORE, json=req.model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["rating_only_ids"]) == [1, 2]
    by_id = {s["review_id"]: s for s in body["scores"]}
    assert by_id[1]["sentiment"] == "POSITIVE"  # 5 stars
    assert by_id[2]["sentiment"] == "NEGATIVE"  # 1 star
    assert by_id[1]["aspects"] == []
    # only review 3 went to the LLM
    assert len(calls) == 1
    assert [r["review_id"] for r in calls[0]["reviews"]] == [3]


async def test_phone_numbers_redacted_before_llm(ai_client, monkeypatch):
    seen = {}

    async def fake_acompletion(**kwargs):
        seen["payload"] = kwargs["messages"][-1]["content"]
        return _echo_scores(kwargs)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    req = _score_req(n=1, text="Great dosa! Call me back on +91 98123 45678 please.")
    resp = await ai_client.post(SCORE, json=req.model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200
    assert "98123" not in seen["payload"]
    assert "[phone]" in seen["payload"]


async def test_score_fans_out_in_chunks(ai_client, monkeypatch):
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(json.loads(kwargs["messages"][-1]["content"]))
        return _echo_scores(kwargs)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    n = REVIEW_SCORE_CHUNK_SIZE + 3
    resp = await ai_client.post(SCORE, json=_score_req(n).model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200
    assert len(calls) == 2
    assert len(resp.json()["scores"]) == n


async def test_all_chunks_dead_is_502(ai_client, monkeypatch):
    async def fake_acompletion(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(SCORE, json=_score_req().model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 502


async def test_all_rating_only_needs_no_llm_at_all(ai_client, monkeypatch):
    async def fake_acompletion(**kwargs):
        raise AssertionError("must not be called")

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    req = ReviewScoreRequest(reviews=[ReviewScoreSourceItem(review_id=1, rating=4, text="")])
    resp = await ai_client.post(SCORE, json=req.model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200
    assert resp.json()["model"] is None


# ------------------------------------------------- local INT8 champion (slice 4)


def _use_fake_champion(monkeypatch, by_text) -> FakeChampion:
    champ = FakeChampion(by_text)
    monkeypatch.setattr(reviews_router, "local_champion", lambda: champ)
    monkeypatch.setattr(reviews_router, "predict_sentiment", _fake_predict)
    return champ


async def test_local_confident_scores_skip_the_llm_entirely(ai_client, monkeypatch):
    async def fake_acompletion(**kwargs):
        raise AssertionError("LLM must not be called for confident local scores")

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    champ = _use_fake_champion(
        monkeypatch,
        {
            "Crispy dosa, loved it.": SentimentPrediction(
                labels=("taste:POSITIVE",), confident=True
            ),
            "Late and cold.": SentimentPrediction(
                labels=("delivery:NEGATIVE", "temperature:NEGATIVE"), confident=True
            ),
        },
    )
    req = ReviewScoreRequest(
        reviews=[
            ReviewScoreSourceItem(review_id=1, rating=5, text="Crispy dosa, loved it."),
            ReviewScoreSourceItem(review_id=2, rating=2, text="Late and cold."),
        ]
    )
    resp = await ai_client.post(SCORE, json=req.model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["local_ids"]) == [1, 2]
    assert body["local_model"] == "local:dosadash-sentiment/v2-int8"
    assert body["model"] is None  # no LLM was involved
    by_id = {s["review_id"]: s for s in body["scores"]}
    assert by_id[1]["sentiment"] == "POSITIVE"
    assert by_id[2]["sentiment"] == "NEGATIVE"  # rollup computed, not model-claimed
    assert {a["aspect"] for a in by_id[2]["aspects"]} == {"delivery", "temperature"}
    assert champ.seen == ["Crispy dosa, loved it.", "Late and cold."]


async def test_local_unconfident_escalates_to_the_llm(ai_client, monkeypatch):
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(json.loads(kwargs["messages"][-1]["content"]))
        return _echo_scores(kwargs)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    _use_fake_champion(
        monkeypatch,
        {
            "Crispy dosa, loved it.": SentimentPrediction(
                labels=("taste:POSITIVE",), confident=True
            ),
            "Hmm, hard to say.": SentimentPrediction(labels=(), confident=False),
        },
    )
    req = ReviewScoreRequest(
        reviews=[
            ReviewScoreSourceItem(review_id=1, rating=5, text="Crispy dosa, loved it."),
            ReviewScoreSourceItem(review_id=2, rating=3, text="Hmm, hard to say."),
        ]
    )
    resp = await ai_client.post(SCORE, json=req.model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["local_ids"] == [1]
    assert body["model"] == "gpt-4o-mini"  # the doubt went to the chain
    assert len(calls) == 1
    assert [r["review_id"] for r in calls[0]["reviews"]] == [2]


async def test_local_contradictory_polarities_escalate(ai_client, monkeypatch):
    """Confident flag but both polarities for one aspect → not trustworthy,
    the LLM gets a second opinion."""
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(json.loads(kwargs["messages"][-1]["content"]))
        return _echo_scores(kwargs)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    _use_fake_champion(
        monkeypatch,
        {
            "Weird one.": SentimentPrediction(
                labels=("taste:NEGATIVE", "taste:POSITIVE"), confident=True
            )
        },
    )
    req = _score_req(n=1, text="Weird one.")
    resp = await ai_client.post(SCORE, json=req.model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["local_ids"] == []
    assert len(calls) == 1


async def test_force_llm_bypasses_the_local_champion(ai_client, monkeypatch):
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(1)
        return _echo_scores(kwargs)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    champ = _use_fake_champion(
        monkeypatch,
        {"Late delivery.": SentimentPrediction(labels=("delivery:NEGATIVE",), confident=True)},
    )
    req = ReviewScoreRequest(
        reviews=[ReviewScoreSourceItem(review_id=1, rating=2, text="Late delivery.")],
        force_llm=True,
    )
    resp = await ai_client.post(SCORE, json=req.model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["local_ids"] == [] and body["local_model"] is None
    assert calls and champ.seen == []


async def test_missing_artifact_degrades_to_llm_never_crashes(ai_client, monkeypatch):
    """local_champion() returning None (artifact missing/corrupt) must leave
    the LLM path fully in charge — the autouse fixture already simulates
    this; assert the shape explicitly."""

    async def fake_acompletion(**kwargs):
        return _echo_scores(kwargs)

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(SCORE, json=_score_req().model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["local_ids"] == [] and body["local_model"] is None
    assert len(body["scores"]) == 2 and body["model"] == "gpt-4o-mini"


# ------------------------------------------------- provider Batch API (slice 5)

BATCH_SUBMIT = "/internal/reviews/batch-submit"
BATCH_POLL = "/internal/reviews/batch-poll"


async def test_defer_llm_returns_deferred_ids_and_never_502(ai_client, monkeypatch):
    """Nightly bulk mode: no local champion → everything with text is
    deferred; the live LLM must NOT be called and deferring is success."""

    async def fake_acompletion(**kwargs):
        raise AssertionError("live LLM must not be called in defer mode")

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    req = ReviewScoreRequest(
        reviews=[
            ReviewScoreSourceItem(review_id=1, rating=5, text=""),
            ReviewScoreSourceItem(review_id=2, rating=2, text="Cold vada."),
        ],
        defer_llm=True,
    )
    resp = await ai_client.post(SCORE, json=req.model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rating_only_ids"] == [1]  # stars still scored deterministically
    assert body["deferred_ids"] == [2]
    assert body["model"] is None


async def test_defer_llm_local_confident_still_scores(ai_client, monkeypatch):
    _use_fake_champion(
        monkeypatch,
        {
            "Crispy dosa.": SentimentPrediction(labels=("taste:POSITIVE",), confident=True),
            "Hmm.": SentimentPrediction(labels=(), confident=False),
        },
    )
    req = ReviewScoreRequest(
        reviews=[
            ReviewScoreSourceItem(review_id=1, rating=5, text="Crispy dosa."),
            ReviewScoreSourceItem(review_id=2, rating=3, text="Hmm."),
        ],
        defer_llm=True,
    )
    resp = await ai_client.post(SCORE, json=req.model_dump(mode="json"), headers=TOKEN)
    body = resp.json()
    assert body["local_ids"] == [1]
    assert body["deferred_ids"] == [2]


async def test_batch_submit_builds_redacted_chunked_jsonl(ai_client, monkeypatch):
    """Rule 8 applies to batch FILES: the uploaded JSONL must carry
    redacted text; chunking mirrors live scoring."""
    captured = {}

    async def fake_create(jsonl, *, completion_window):
        captured["jsonl"] = jsonl.decode()
        captured["window"] = completion_window
        return "batch_abc123"

    monkeypatch.setattr(reviews_router, "create_chat_batch", fake_create)
    n = REVIEW_SCORE_CHUNK_SIZE + 2
    reviews = [
        {"review_id": i, "rating": 2, "text": f"Late. Call me on +91 98123 4567{i % 10}."}
        for i in range(1, n + 1)
    ]
    resp = await ai_client.post(BATCH_SUBMIT, json={"reviews": reviews}, headers=TOKEN)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider_batch_id"] == "batch_abc123"
    assert body["model"] == "gpt-4o-mini"
    assert body["n_reviews"] == n
    assert [len(c) for c in body["chunks"]] == [REVIEW_SCORE_CHUNK_SIZE, 2]

    lines = [json.loads(line) for line in captured["jsonl"].strip().splitlines()]
    assert [entry["custom_id"] for entry in lines] == ["chunk-0", "chunk-1"]
    assert all(entry["url"] == "/v1/chat/completions" for entry in lines)
    assert "98123" not in captured["jsonl"]  # phones never land in the file
    assert "[phone]" in captured["jsonl"]
    assert captured["window"] == "24h"


async def test_batch_submit_provider_failure_is_502(ai_client, monkeypatch):
    from dosadash_ai.llm.batch import BatchError

    async def fake_create(jsonl, *, completion_window):
        raise BatchError("no OPENAI_API_KEY")

    monkeypatch.setattr(reviews_router, "create_chat_batch", fake_create)
    resp = await ai_client.post(
        BATCH_SUBMIT,
        json={"reviews": [{"review_id": 1, "rating": 2, "text": "Late."}]},
        headers=TOKEN,
    )
    assert resp.status_code == 502


def _batch_line(custom_id: str, scores: list[dict]) -> str:
    return json.dumps(
        {
            "custom_id": custom_id,
            "response": {
                "status_code": 200,
                "body": {"choices": [{"message": {"content": json.dumps({"scores": scores})}}]},
            },
            "error": None,
        }
    )


async def test_batch_poll_completed_runs_the_same_guardrail(ai_client, monkeypatch):
    """Hallucinated review_ids and off-registry aspects in batch output are
    dropped exactly as in live scoring; unanswered reviews are rejected."""

    async def fake_retrieve(batch_id):
        assert batch_id == "batch_abc123"
        lines = [
            _batch_line(
                "chunk-0",
                [
                    {
                        "review_id": 11,
                        "sentiment": "NEGATIVE",
                        "aspects": [
                            {"aspect": "delivery", "sentiment": "NEGATIVE"},
                            {"aspect": "vibes", "sentiment": "NEGATIVE"},  # off-registry
                        ],
                    },
                    {  # hallucinated id — the api never sent 999
                        "review_id": 999,
                        "sentiment": "NEGATIVE",
                        "aspects": [{"aspect": "taste", "sentiment": "NEGATIVE"}],
                    },
                ],
            )
        ]
        return "completed", lines, None

    monkeypatch.setattr(reviews_router, "retrieve_chat_batch", fake_retrieve)
    resp = await ai_client.post(
        BATCH_POLL,
        json={"provider_batch_id": "batch_abc123", "chunks": [[11, 12]]},
        headers=TOKEN,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert [s["review_id"] for s in body["scores"]] == [11]
    assert [a["aspect"] for a in body["scores"][0]["aspects"]] == ["delivery"]
    assert [r["review_id"] for r in body["rejected"]] == [12]  # unanswered
    assert body["model"] == "gpt-4o-mini"


async def test_batch_poll_in_progress_and_failed_pass_through(ai_client, monkeypatch):
    async def in_progress(batch_id):
        return "in_progress", None, None

    monkeypatch.setattr(reviews_router, "retrieve_chat_batch", in_progress)
    resp = await ai_client.post(
        BATCH_POLL, json={"provider_batch_id": "b1", "chunks": [[1]]}, headers=TOKEN
    )
    assert resp.json()["status"] == "in_progress"

    async def failed(batch_id):
        return "failed", None, "provider status expired"

    monkeypatch.setattr(reviews_router, "retrieve_chat_batch", failed)
    resp = await ai_client.post(
        BATCH_POLL, json={"provider_batch_id": "b1", "chunks": [[1]]}, headers=TOKEN
    )
    body = resp.json()
    assert body["status"] == "failed"
    assert "expired" in body["error"]


async def test_batch_poll_malformed_line_rejects_its_chunk_only(ai_client, monkeypatch):
    async def fake_retrieve(batch_id):
        lines = [
            "not json at all",
            _batch_line(
                "chunk-1",
                [
                    {
                        "review_id": 21,
                        "sentiment": "POSITIVE",
                        "aspects": [{"aspect": "taste", "sentiment": "POSITIVE"}],
                    }
                ],
            ),
        ]
        return "completed", lines, None

    monkeypatch.setattr(reviews_router, "retrieve_chat_batch", fake_retrieve)
    resp = await ai_client.post(
        BATCH_POLL,
        json={"provider_batch_id": "b2", "chunks": [[11], [21]]},
        headers=TOKEN,
    )
    body = resp.json()
    assert [s["review_id"] for s in body["scores"]] == [21]
    assert [r["review_id"] for r in body["rejected"]] == [11]


async def test_batch_endpoints_require_internal_token(ai_client):
    body = {"reviews": [{"review_id": 1, "rating": 2, "text": "x"}]}
    assert (await ai_client.post(BATCH_SUBMIT, json=body)).status_code == 403
    poll = {"provider_batch_id": "b", "chunks": []}
    assert (await ai_client.post(BATCH_POLL, json=poll)).status_code == 403


# ------------------------------------------------------------------- replies


async def test_reply_clean_draft_ships(ai_client, monkeypatch):
    async def fake_acompletion(**kwargs):
        draft = "So sorry about the leak — sturdier boxes are coming this week. — Team DosaDash"
        return FakeResponse(json.dumps({"reply": draft}))

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(REPLY, json=_reply_req().model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["fallback"] is False
    assert body["reply"].startswith("So sorry")
    assert body["prompt_version"] == "review_reply_v1"


async def test_reply_compensation_promise_falls_back(ai_client, monkeypatch):
    """The model promises a refund → deterministic template ships instead."""

    async def fake_acompletion(**kwargs):
        return FakeResponse(json.dumps({"reply": "We are so sorry — we will refund you fully!"}))

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(REPLY, json=_reply_req().model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["fallback"] is True
    assert "refund" not in body["reply"].lower()
    assert body["reply"].endswith("— Team DosaDash")


async def test_reply_llm_failure_falls_back_never_5xx(ai_client, monkeypatch):
    async def fake_acompletion(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(REPLY, json=_reply_req().model_dump(mode="json"), headers=TOKEN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["fallback"] is True
    assert body["model"] is None


async def test_reply_fallback_matches_sentiment(ai_client, monkeypatch):
    async def fake_acompletion(**kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(
        REPLY,
        json=_reply_req(rating=5, sentiment="POSITIVE", text="Loved it!").model_dump(mode="json"),
        headers=TOKEN,
    )
    assert "Thank you" in resp.json()["reply"]

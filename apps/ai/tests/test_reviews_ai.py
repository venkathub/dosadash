"""Review scoring + reply chain tests (Phase 8): endpoint auth, rating-only
split, redaction-before-LLM, guardrail end-to-end, and the reply fallback —
litellm is always mocked (CI never calls providers). Pure sanitizer cases
live in evals/suites/test_review_assets.py."""

import json

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.llm import client as llm_client
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

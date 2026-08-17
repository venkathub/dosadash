"""SSE streaming tests: reply extraction, fallback, prefix stability."""

import json

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.agent import graph as graph_mod
from dosadash_ai.agent import streaming as streaming_mod
from dosadash_ai.agent.streaming import ReplyExtractor, stream_turn
from dosadash_ai.db import get_session
from dosadash_ai.llm.client import LLMError
from dosadash_shared import AgentChatRequest, AgentTurn, DraftItemIn

# ------------------------------------------------------------ ReplyExtractor


def _feed_all(chunks: list[str]) -> str:
    ex = ReplyExtractor()
    return "".join(ex.feed(c) for c in chunks)


def test_extracts_reply_across_chunks():
    chunks = ['{"re', 'ply": "Two Masa', "la Dosas added!", '", "draft_items": []}']
    assert _feed_all(chunks) == "Two Masala Dosas added!"


def test_handles_escapes_split_across_chunks():
    chunks = ['{"reply": "line1\\', 'nline2 \\"quoted\\', '" end", "x": 1}']
    assert _feed_all(chunks) == 'line1\nline2 "quoted" end'


def test_handles_unicode_escape_split():
    chunks = ['{"reply": "hi \\u0939', 'indi", "x": 1}']
    assert _feed_all(chunks) == "hi हindi"


def test_stops_at_closing_quote():
    ex = ReplyExtractor()
    out = ex.feed('{"reply": "done", "draft_items": [{"notes": "not reply text"}]}')
    assert out == "done"
    assert ex.feed(' more {"reply": "again"}') == ""  # done stays done


def test_no_reply_key_yields_nothing():
    assert _feed_all(['{"other": "value"}']) == ""


# ----------------------------------------------------------------- stream_turn


@pytest.fixture
def agent_stream_env(monkeypatch):
    """Fake retrieval/embedding; per-test control of the stream + fallback."""
    state = {
        "chunks": None,  # list[str] → streamed; None → stream raises LLMError
        "fallback_turn": AgentTurn(reply="fallback reply"),
        "fallback_calls": 0,
        "fallback_raises": False,
    }

    async def fake_embed(texts, **_):
        return [[0.0] * 1536 for _ in texts]

    async def fake_stream(**kwargs):
        if state["chunks"] is None:
            raise LLMError("stream down")
        for piece in state["chunks"]:
            yield piece

    def fake_stream_gen(**kwargs):
        return fake_stream(**kwargs)

    async def fake_structured(**kwargs):
        state["fallback_calls"] += 1
        if state["fallback_raises"]:
            raise LLMError("chain down")
        return state["fallback_turn"], "groq/llama-3.3-70b-versatile"

    monkeypatch.setattr(graph_mod, "embed_texts", fake_embed)
    monkeypatch.setattr(streaming_mod, "stream_text_completion", fake_stream_gen)
    monkeypatch.setattr(streaming_mod, "structured_completion", fake_structured)
    return state


async def _collect(session, request):
    return [event async for event in stream_turn(session, request)]


TURN_JSON = json.dumps(
    {
        "reply": "Two Masala Dosas added!",
        "draft_items": [{"item_id": 1, "qty": 2, "notes": None}],
        "ready_to_place": False,
    }
)


async def test_stream_happy_path(agent_session, agent_stream_env):
    agent_stream_env["chunks"] = [TURN_JSON[i : i + 7] for i in range(0, len(TURN_JSON), 7)]
    events = await _collect(agent_session, AgentChatRequest(message="2 masala dosas"))
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "Two Masala Dosas added!"
    final = events[-1]
    assert final["type"] == "final"
    assert final["data"]["draft"]["items"][0]["name"] == "Masala Dosa"  # guardrail ran
    assert final["data"]["draft"]["items"][0]["unit_price"] == "120.00"
    assert agent_stream_env["fallback_calls"] == 0


async def test_stream_validates_hallucinated_items(agent_session, agent_stream_env):
    bad = json.dumps(
        {"reply": "ok", "draft_items": [{"item_id": 999, "qty": 1}], "ready_to_place": True}
    )
    agent_stream_env["chunks"] = [bad]
    events = await _collect(agent_session, AgentChatRequest(message="pizza"))
    final = events[-1]["data"]
    assert final["draft"]["items"] == []  # Hard Rule 2 also guards the stream path
    assert final["ready_to_place"] is False


async def test_stream_failure_falls_back_to_chain(agent_session, agent_stream_env):
    agent_stream_env["chunks"] = None  # stream errors immediately
    agent_stream_env["fallback_turn"] = AgentTurn(
        reply="fallback reply", draft_items=[DraftItemIn(item_id=3, qty=1)]
    )
    events = await _collect(agent_session, AgentChatRequest(message="a coffee"))
    assert [e["type"] for e in events] == ["final"]  # no deltas, still a full answer
    assert events[0]["data"]["reply"] == "fallback reply"
    assert events[0]["data"]["model"] == "groq/llama-3.3-70b-versatile"
    assert agent_stream_env["fallback_calls"] == 1


async def test_invalid_stream_json_falls_back(agent_session, agent_stream_env):
    agent_stream_env["chunks"] = ['{"reply": "half a json...']
    events = await _collect(agent_session, AgentChatRequest(message="hi"))
    assert events[-1]["type"] == "final"
    assert agent_stream_env["fallback_calls"] == 1


async def test_total_failure_emits_error_event(agent_session, agent_stream_env):
    agent_stream_env["chunks"] = None
    agent_stream_env["fallback_raises"] = True
    events = await _collect(agent_session, AgentChatRequest(message="hi"))
    assert events == [{"type": "error", "detail": "LLM chain failed: chain down"}]


# ------------------------------------------------------------- SSE endpoint


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
    monkeypatch.setenv("AI_INTERNAL_API_TOKEN", "test-internal-token")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


async def test_sse_endpoint_frames_events(monkeypatch):
    from dosadash_ai.main import app
    from dosadash_ai.routers import agent as agent_router

    async def fake_stream(session, req):
        yield {"type": "delta", "text": "Hi"}
        yield {"type": "final", "data": {"reply": "Hi"}}

    monkeypatch.setattr(agent_router, "stream_turn", fake_stream)

    async def fake_session():
        yield None

    app.dependency_overrides[get_session] = fake_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/agent/chat/stream",
            json={"message": "hello"},
            headers={"X-Internal-Token": "test-internal-token"},
        )
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = [line for line in resp.text.split("\n\n") if line]
    assert frames[0] == 'data: {"type": "delta", "text": "Hi"}'
    assert json.loads(frames[1].removeprefix("data: "))["type"] == "final"


async def test_sse_endpoint_requires_token():
    from dosadash_ai.main import app

    async def fake_session():
        yield None

    app.dependency_overrides[get_session] = fake_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/internal/agent/chat/stream", json={"message": "hello"})
    app.dependency_overrides.clear()
    assert resp.status_code == 403


# ------------------------------------------------- prompt-caching contract


async def test_menu_prefix_is_stable_across_turns_and_users(agent_session, monkeypatch):
    """[system prompt, MENU] must be byte-identical across requests —
    that's what makes provider prefix caching hit."""
    captured: list[list[dict]] = []

    async def fake_completion(**kwargs):
        captured.append(kwargs["messages"])
        return AgentTurn(reply="ok"), "gpt-4o-mini"

    async def fake_embed(texts, **_):
        return [[0.0] * 1536 for _ in texts]

    monkeypatch.setattr(graph_mod, "structured_completion", fake_completion)
    monkeypatch.setattr(graph_mod, "embed_texts", fake_embed)

    from dosadash_ai.agent.graph import run_turn

    await run_turn(agent_session, AgentChatRequest(message="2 dosas"))
    await run_turn(agent_session, AgentChatRequest(message="a coffee", user_id=7))
    first, second = captured
    assert first[0] == second[0]  # static system prompt
    assert first[1] == second[1]  # MENU context — cacheable prefix
    assert first[2] != second[2]  # volatile STATE differs (prefs)

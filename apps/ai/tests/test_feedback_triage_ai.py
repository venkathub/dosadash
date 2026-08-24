"""Feedback triage endpoint (Phase 13 slice 3): chain → policy → fallback.
Pure-policy cases live in evals/suites/test_feedback_triage_assets.py."""

import json

import httpx
import pytest

from dosadash_ai import config
from dosadash_ai.llm import client as llm_client

TOKEN = {"X-Internal-Token": "test-internal-token"}


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


class FakeResponse:
    def __init__(self, content: str) -> None:
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message})()]


def _assessment(**overrides) -> str:
    base = {
        "actionable": True,
        "type": "BUG",
        "severity": "LOW",
        "effort": "S",
        "risk": "LOW",
        "area": "apps/web",
        "summary": "typo on checkout button",
    }
    base.update(overrides)
    return json.dumps(base)


def _request(**overrides) -> dict:
    base = {
        "report_id": 7,
        "type": "BUG",
        "title": "Typo on checkout",
        "description": "Button says Procede instead of Proceed.",
        "reporter_tier": "CUSTOMER",
    }
    base.update(overrides)
    return base


async def test_requires_internal_token(ai_client) -> None:
    resp = await ai_client.post("/internal/feedback/triage", json=_request())
    assert resp.status_code == 403


async def test_small_low_risk_bug_auto_fixes(ai_client, monkeypatch) -> None:
    async def fake_acompletion(**kwargs):
        return FakeResponse(_assessment())

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post("/internal/feedback/triage", json=_request(), headers=TOKEN)
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "AUTO_FIX"
    assert body["labels"] == ["ai:auto-fix"]
    assert body["fallback"] is False
    assert body["assessment"]["summary"] == "typo on checkout button"
    assert body["model"]  # provenance recorded


async def test_feature_filing_never_auto_fixes_even_if_model_says_bug(
    ai_client, monkeypatch
) -> None:
    """Injection defence: a FEATURE report whose text talks the model into
    a S/LOW BUG assessment is still approval-gated by the policy."""

    async def fake_acompletion(**kwargs):
        return FakeResponse(_assessment())  # model claims S/LOW BUG

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post(
        "/internal/feedback/triage", json=_request(type="FEATURE"), headers=TOKEN
    )
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "NEEDS_APPROVAL"
    assert resp.json()["violations"]


async def test_not_actionable_dismisses_with_no_labels(ai_client, monkeypatch) -> None:
    async def fake_acompletion(**kwargs):
        return FakeResponse(_assessment(actionable=False))

    monkeypatch.setattr(llm_client.litellm, "acompletion", fake_acompletion)
    resp = await ai_client.post("/internal/feedback/triage", json=_request(), headers=TOKEN)
    assert resp.json()["verdict"] == "DISMISS"
    assert resp.json()["labels"] == []


async def test_chain_failure_degrades_to_needs_approval_not_502(ai_client, monkeypatch) -> None:
    async def dead_acompletion(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_client.litellm, "acompletion", dead_acompletion)
    resp = await ai_client.post("/internal/feedback/triage", json=_request(), headers=TOKEN)
    assert resp.status_code == 200  # deferring to a human is success
    body = resp.json()
    assert body["verdict"] == "NEEDS_APPROVAL"
    assert body["fallback"] is True
    assert body["labels"] == ["ai:needs-approval"]
    assert body["assessment"] is None


async def test_phone_redacted_before_llm(ai_client, monkeypatch) -> None:
    seen: list[dict] = []

    async def spy_acompletion(**kwargs):
        seen.extend(kwargs["messages"])
        return FakeResponse(_assessment())

    monkeypatch.setattr(llm_client.litellm, "acompletion", spy_acompletion)
    resp = await ai_client.post(
        "/internal/feedback/triage",
        json=_request(description="Call me on +91 98765 43210, checkout typo."),
        headers=TOKEN,
    )
    assert resp.status_code == 200
    assert seen
    for message in seen:
        assert "98765" not in message["content"]

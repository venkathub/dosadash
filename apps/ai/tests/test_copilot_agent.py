"""Copilot agent flow: self-correction loop, chart fallback, LLM failure."""

import pytest

from dosadash_ai.copilot import agent as agent_mod
from dosadash_shared import CopilotChart, CopilotDraft

GOOD_SQL = "SELECT category, COUNT(*) AS n FROM menu_items GROUP BY category LIMIT 10"
BAD_SQL = "SELECT token_hash FROM refresh_tokens LIMIT 5"  # table not allowlisted


class FakeLLM:
    """Yields queued drafts; records the conversation it was given."""

    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.calls = []

    async def __call__(self, *, messages, response_model, **kwargs):
        self.calls.append([dict(m) for m in messages])
        return self.drafts.pop(0), "test-model"


@pytest.fixture
def fake_run(monkeypatch):
    async def run(sql):
        return ["category", "n"], [["Dosa", 10], ["Biryani", 4]]

    monkeypatch.setattr(agent_mod, "_run_readonly", run)


def _draft(sql, chart=None):
    return CopilotDraft(
        sql=sql, explanation="test", chart=chart or CopilotChart(type="bar", x="category", y="n")
    )


async def test_valid_first_draft(monkeypatch, fake_run):
    llm = FakeLLM([_draft(GOOD_SQL)])
    monkeypatch.setattr(agent_mod, "structured_completion", llm)
    answer = await agent_mod.ask("dishes per category")
    assert answer.error is None
    assert answer.attempts == 1
    assert answer.rows == [["Dosa", 10], ["Biryani", 4]]
    assert answer.sql is not None and "LIMIT" in answer.sql
    assert answer.chart.type == "bar"


async def test_self_correction_feeds_error_back(monkeypatch, fake_run):
    llm = FakeLLM([_draft(BAD_SQL), _draft(GOOD_SQL)])
    monkeypatch.setattr(agent_mod, "structured_completion", llm)
    answer = await agent_mod.ask("dishes per category")
    assert answer.error is None and answer.attempts == 2
    # Second call must contain the failure feedback
    feedback = llm.calls[1][-1]["content"]
    assert "failed" in feedback and "allowlist" in feedback


async def test_gives_up_after_max_attempts(monkeypatch, fake_run):
    llm = FakeLLM([_draft(BAD_SQL)] * agent_mod.MAX_ATTEMPTS)
    monkeypatch.setattr(agent_mod, "structured_completion", llm)
    answer = await agent_mod.ask("nuke the orders table")
    assert answer.error is not None
    assert answer.attempts == agent_mod.MAX_ATTEMPTS
    assert answer.rows == []


async def test_chart_falls_back_when_y_column_missing(monkeypatch, fake_run):
    llm = FakeLLM([_draft(GOOD_SQL, chart=CopilotChart(type="bar", x="category", y="revenue"))])
    monkeypatch.setattr(agent_mod, "structured_completion", llm)
    answer = await agent_mod.ask("dishes per category")
    assert answer.chart.type == "none"  # y col not in result → no broken chart


async def test_llm_failure_is_reported(monkeypatch, fake_run):
    async def broken(**kwargs):
        raise agent_mod.LLMError("all models failed")

    monkeypatch.setattr(agent_mod, "structured_completion", broken)
    answer = await agent_mod.ask("anything")
    assert answer.error == "all models failed"

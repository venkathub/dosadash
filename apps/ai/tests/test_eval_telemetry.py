"""The optional collector must observe real calls without changing the provider contract."""

from types import SimpleNamespace as NS

from pydantic import BaseModel

from dosadash_ai.llm import client
from dosadash_ai.llm.eval_telemetry import EvalTelemetry, active_telemetry


class Reply(BaseModel):
    answer: str


async def test_collects_usage_without_writing_shared_redis_stats(monkeypatch):
    async def completion(**kwargs):
        return NS(choices=[NS(message=NS(content='{"answer":"ok"}'))], usage=NS(total_tokens=17))

    def forbidden():
        raise AssertionError("evaluation must not update shared production counters")

    monkeypatch.setattr(client.litellm, "acompletion", completion)
    monkeypatch.setattr(client.litellm, "completion_cost", lambda **kw: 0.001)
    monkeypatch.setattr(client, "get_usage_stats", forbidden)
    ledger = EvalTelemetry()
    token = active_telemetry.set(ledger)
    try:
        reply, model = await client.structured_completion(
            messages=[],
            response_model=Reply,
            trace_name="test",
            prompt_version="v1",
            models=["model"],
        )
    finally:
        active_telemetry.reset(token)
    assert reply.answer == "ok" and model == "model"
    assert ledger.summary()["tokens"] == 17
    assert ledger.summary()["estimated_cost_usd"] == 0.001

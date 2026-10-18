"""Opt-in, context-local evaluation accounting. Never stores prompts or credentials."""

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import litellm


class EvalBudgetExceeded(RuntimeError):
    """Stops a run before its next provider request (one in-flight call may overshoot)."""


@dataclass
class EvalTelemetry:
    max_calls: int = 1000
    max_tokens: int = 2_000_000
    max_cost_usd: float = 5.0
    calls: list[dict[str, Any]] = field(default_factory=list)
    attempted: int = 0

    def before_call(self) -> float:
        totals = self.summary()
        if (
            self.attempted >= self.max_calls
            or totals["known_tokens"] >= self.max_tokens
            or totals["known_cost_usd"] >= self.max_cost_usd
        ):
            raise EvalBudgetExceeded("evaluation provider budget exhausted")
        self.attempted += 1
        return time.monotonic()

    def record(
        self,
        started: float,
        model: str,
        version: str,
        response: Any = None,
        error: str | None = None,
    ) -> None:
        usage = getattr(response, "usage", None)
        tokens = getattr(usage, "total_tokens", None)
        cost = None
        if response is not None:
            try:
                cost = litellm.completion_cost(completion_response=response, model=model)
            except Exception:  # noqa: BLE001 — absent provider pricing is unknown, not free
                pass
        self.calls.append(
            {
                "case_id": active_case.get(),
                "model": model,
                "prompt_version": version,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "tokens": tokens,
                "estimated_cost_usd": cost,
                "error": error,
            }
        )

    def summary(self) -> dict[str, Any]:
        known_tokens = sum(c["tokens"] or 0 for c in self.calls)
        known_cost = sum(c["estimated_cost_usd"] or 0 for c in self.calls)
        return {
            "attempted_calls": self.attempted,
            "completed_calls": len(self.calls),
            "known_tokens": known_tokens,
            "known_cost_usd": known_cost,
            "tokens": known_tokens
            if self.calls
            and len(self.calls) == self.attempted
            and all(c["tokens"] is not None for c in self.calls)
            else None,
            "estimated_cost_usd": known_cost
            if self.calls
            and len(self.calls) == self.attempted
            and all(c["estimated_cost_usd"] is not None for c in self.calls)
            else None,
            "models": sorted({c["model"] for c in self.calls}),
            "calls": self.calls,
        }


active_telemetry: ContextVar[EvalTelemetry | None] = ContextVar("eval_telemetry", default=None)

active_case: ContextVar[str | None] = ContextVar("eval_case", default=None)

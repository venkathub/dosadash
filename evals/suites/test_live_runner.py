"""Regression tests for fail-closed execution, budgets and truthful reports."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _results import SuiteResult, evaluate_cases, require_eval_database
from run_live_evals import run

from dosadash_ai.config import get_settings
from dosadash_ai.llm.eval_telemetry import EvalBudgetExceeded, EvalTelemetry
from dosadash_shared import EvalRunIn


async def test_empty_and_exception_are_errors_even_for_advisory_suites():
    async def broken(case):
        raise RuntimeError("provider unavailable")

    empty = await evaluate_cases("rag", [], broken)
    failed = await evaluate_cases("rag", [{"id": "x"}], broken)
    assert empty.status == failed.status == "error"
    assert empty.gate_failed and failed.gate_failed
    assert failed.cases[0]["error"] == "RuntimeError"


async def test_timeout_does_not_suppress_later_cases():
    async def execute(case):
        if case["id"] == "slow":
            await asyncio.sleep(10)
        return {"problems": []}

    result = await evaluate_cases(
        "retrieval", [{"id": "slow"}, {"id": "ok"}], execute, case_timeout=0.01
    )
    assert [c["status"] for c in result.cases] == ["error", "pass"]
    assert result.gate_failed


def test_advisory_quality_does_not_hide_safety_bypass():
    assert not SuiteResult(name="rag", status="fail").gate_failed
    assert SuiteResult(name="support", status="fail", metrics={"bypasses": 1}).gate_failed
    assert SuiteResult(name="order", status="fail", blocking=True).gate_failed
    assert SuiteResult(name="rag", status="skipped").gate_failed


def test_unknown_usage_remains_null_and_budget_counts_attempts():
    ledger = EvalTelemetry(max_calls=1)
    started = ledger.before_call()
    ledger.record(started, "test", "v1", SimpleNamespace())
    assert ledger.summary()["tokens"] is None
    assert ledger.summary()["estimated_cost_usd"] is None
    with pytest.raises(EvalBudgetExceeded):
        ledger.before_call()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@production.example/app_eval",
        "postgresql+asyncpg://u:p@localhost/production",
    ],
)
def test_live_preflight_rejects_unsafe_database(monkeypatch, url):
    monkeypatch.setenv("EVAL_DATABASE_URL", url)
    with pytest.raises(ValueError):
        require_eval_database()


async def test_preflight_failure_still_produces_valid_failed_payload(monkeypatch):
    monkeypatch.delenv("EVAL_DATABASE_URL", raising=False)
    code, report = await run(suites=[])
    assert code == 1
    assert not report["gates_passed"]
    assert report["failures"]
    EvalRunIn.model_validate(report)


def test_new_report_cannot_claim_success_with_failed_required_execution():
    payload = dict(
        ran_at="2026-10-18T00:00:00Z",
        cases=1,
        metrics={},
        gates_passed=True,
        run_metadata={"schema_version": 1},
        suite_results=[{"name": "order", "status": "pass"}, {"name": "rag", "status": "error"}],
    )
    with pytest.raises(ValueError):
        EvalRunIn.model_validate(payload)


def test_ordering_failure_cannot_be_relabeled_advisory_in_successful_report():
    with pytest.raises(ValueError):
        EvalRunIn.model_validate(
            dict(
                ran_at="2026-10-18T00:00:00Z",
                cases=1,
                metrics={},
                gates_passed=True,
                run_metadata={"schema_version": 1},
                suite_results=[{"name": "order", "status": "fail", "blocking": False}],
            )
        )


def test_preflight_disables_effective_cache(monkeypatch):
    url = "postgresql+asyncpg://u:p@localhost/test_eval"
    for key in ("EVAL_DATABASE_URL", "AI_DATABASE_URL", "API_DATABASE_URL"):
        monkeypatch.setenv(key, url)
    settings = get_settings()
    monkeypatch.setattr(settings, "database_url", url)
    monkeypatch.setattr(settings, "readonly_db_password", "")
    monkeypatch.setattr(settings, "semcache_enabled", True)
    require_eval_database()
    assert settings.semcache_enabled is False


async def test_bad_quality_results_are_retained_even_when_advisory(monkeypatch):
    import _results

    config = _results.load_config()
    config["suites"]["copilot"].update(blocking=False, calibrated=False)
    monkeypatch.setattr(_results, "load_config", lambda: config)

    async def incorrect(case):
        return {"problems": ["wrong result"]}

    result = await evaluate_cases("copilot", [{"id": "bad"}], incorrect)
    assert result.status == "fail" and not result.gate_failed
    assert result.cases[0]["problems"] == ["wrong result"]

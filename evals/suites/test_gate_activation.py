"""Threshold boundaries and replay of retained baselines under the active policy."""

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _results import SuiteResult, evaluate_cases, load_config


@pytest.mark.parametrize(
    "name,total", [("retrieval", 23), ("support", 6), ("inventory", 5), ("copilot", 6)]
)
async def test_deterministic_quality_gates_block_below_threshold(name, total):
    minimum = math.ceil(load_config()["suites"][name]["threshold"] * total)

    async def passing(case):
        return {"problems": [] if case["id"] < minimum else ["known incorrect output"]}

    async def failing(case):
        return {"problems": [] if case["id"] < minimum - 1 else ["known incorrect output"]}

    cases = [{"id": i} for i in range(total)]
    accepted = await evaluate_cases(name, cases, passing)
    rejected = await evaluate_cases(name, cases, failing)
    assert accepted.status == "pass" and not accepted.gate_failed
    assert rejected.status == "fail" and rejected.gate_failed


@pytest.mark.parametrize("number", [1, 2])
def test_retained_final_baselines_pass_current_gate_policy(number):
    path = Path(__file__).resolve().parents[1] / f"baselines/p0-final-frozen-{number}.json"
    report = json.loads(path.read_text())
    policy = load_config()["suites"]
    assert {s["name"] for s in report["suite_results"]} == set(policy)
    for recorded in report["suite_results"]:
        suite = SuiteResult.model_validate(recorded)
        assert suite.status == "pass"
        # Replay gating only; this is not a fresh model run or a rewrite of historical evidence.
        suite.blocking = policy[suite.name]["blocking"]
        suite.calibrated = policy[suite.name]["calibrated"]
        assert not suite.gate_failed

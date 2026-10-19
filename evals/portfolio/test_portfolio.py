"""Contract, scoring negative controls and explicit evidence boundaries."""

import json
from pathlib import Path

import pytest

from .metrics import audio_metrics, invoice_metrics
from .runner import aggregate, asset, read_cases, run

MANIFEST = Path(__file__).parent / "fixtures/manifest.json"


def test_fixtures_are_explicitly_synthetic_and_hash_checked():
    cases = read_cases(MANIFEST)
    assert {c.task for c in cases} == {"review", "audio", "invoice"}
    assert all(c.origin == "synthetic" for c in cases)
    for case in cases:
        if case.path:
            assert asset(case, MANIFEST)
    audio = [c for c in cases if c.task == "audio"]
    assert all(asset(c, MANIFEST).startswith(b"RIFF") for c in audio)
    assert {c.category for c in audio} >= {"noise", "correction"}


def test_audio_negative_controls():
    assert (
        audio_metrics("two dosa no coffee", "two dosa no coffee", ["no coffee"])["word_error_rate"]
        == 0
    )
    score = audio_metrics("two dosa no coffee", "one dosa and coffee", ["no coffee"])
    assert score["word_error_rate"] == 0.5 and score["command_term_recall"] == 0
    assert audio_metrics("one coffee", "", ["coffee"])["word_error_rate"] == 1
    with pytest.raises(ValueError):
        audio_metrics("", "anything", [])


def test_invoice_missing_extra_wrong_total_and_inconsistent_bill():
    case = next(c for c in read_cases(MANIFEST) if c.id == "invoice-0")
    expected = case.expected
    assert invoice_metrics(expected, expected)["exact_match"]
    wrong = json.loads(json.dumps(expected))
    wrong["lines"].pop()
    assert invoice_metrics(expected, wrong)["line_recall"] == 0.5
    wrong = json.loads(json.dumps(expected))
    wrong["lines"].append(wrong["lines"][0])
    assert invoice_metrics(expected, wrong)["line_precision"] < 1
    wrong["total"] = "900"
    assert not invoice_metrics(expected, wrong)["arithmetic_ok"]
    bad_bill = next(c for c in read_cases(MANIFEST) if c.id == "invoice-2")
    score = invoice_metrics(bad_bill.expected, bad_bill.expected)
    assert score["exact_match"] and not score["arithmetic_ok"]  # read accurately, flag arithmetic


async def test_replay_is_never_model_accuracy():
    result = await run(MANIFEST, "replay")
    assert result["status"] == "complete" and result["evidence"] == "synthetic_portfolio_demo"
    assert all(r["model"] == "reference-replay:NO-MODEL-INFERENCE" for r in result["cases"])
    assert result["results"]["invoice"]["overall"]["groups"] == 1
    assert result["results"]["invoice"]["overall"]["ci95"] is None


async def test_provider_failure_is_retained_and_not_scored(monkeypatch):
    async def failed(*args):
        raise RuntimeError("private provider payload")

    from . import runner

    monkeypatch.setattr(runner, "predict", failed)
    monkeypatch.setattr("dosadash_ai.llm.configure_tracing", lambda: None)
    result = await run(MANIFEST, "live")
    assert result["status"] == "error"
    assert all(r["status"] == "error" for r in result["cases"])
    assert "private provider payload" not in json.dumps(result)
    assert result["results"]["audio"]["overall"]["metrics"] is None


def test_escape_hash_mismatch_and_missing_permission(tmp_path):
    case = next(c for c in read_cases(MANIFEST) if c.task == "audio")
    with pytest.raises(ValueError, match="escapes"):
        asset(case.model_copy(update={"path": "../secret.wav"}), MANIFEST)
    with pytest.raises(ValueError, match="changed"):
        asset(case.model_copy(update={"sha256": "0" * 64}), MANIFEST)
    assert aggregate([{"group_id": "x", "status": "error"}])["metrics"] is None


async def test_expanded_downstream_replay_and_discrepancy_negative_controls():
    from .downstream import invoice_decision, order_metrics

    manifest = MANIFEST.parent.parent / "fixtures-expanded/manifest.json"
    cases = read_cases(manifest)
    assert len(cases) == 24
    assert len({c.group_id for c in cases if c.task == "invoice"}) == 4
    result = await run(manifest, "replay")
    assert result["status"] == "complete"
    assert result["results"]["audio"]["overall"]["metrics"]["order_draft_correct"] == 1
    assert result["results"]["invoice"]["overall"]["metrics"]["review_decision_correct"] == 1
    for case in cases:
        if case.task == "invoice":
            decision = invoice_decision(case.expected, case.purchase_order)
            assert decision["status"] == case.expected_review_status
            assert decision["human_approval_required"]
    assert not order_metrics({"items": {"Masala Dosa": 2}}, {"items": {"Masala Dosa": 1}})[
        "order_draft_correct"
    ]
    assert not order_metrics({"items": {}, "notes_contains": ["no sugar"]}, {"items": {}})[
        "order_draft_correct"
    ]


def test_mixed_downstream_coverage_never_reports_partial_as_full_accuracy():
    report = aggregate(
        [
            {
                "group_id": "a",
                "status": "ok",
                "metrics": {"word_error_rate": 0, "order_draft_correct": True},
            },
            {"group_id": "b", "status": "ok", "metrics": {"word_error_rate": 1}},
        ]
    )
    assert report["metrics"] == {"word_error_rate": 0.5}
    assert report["metric_coverage"]["order_draft_correct"] == 1

"""Invented unit-test fixtures simulate provenance; none are benchmark evidence."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from . import dataset, review_runner
from .contracts import Adjudication, Annotation, Protocol, ReviewRecord
from .dataset import OverlapIndex, audit, fingerprint, freeze, load_records
from .metrics import paired_delta, report_metrics


def record(id="v1", split="val", **changes):
    values = dict(
        id=id,
        group_id=id,
        split=split,
        text="I kept the second helping for tomorrow because this seasoning made me smile",
        rating=4,
        language="en",
        categories=["unseen_phrasing"],
        origin="human_authored",
        source_ref="unit-test-only",
        permission_ref="simulated-unit-test-permission",
        author="simulated-author",
        pii_reviewed=True,
        independent_of_training=True,
        annotations=[
            Annotation(method="human", reviewer=r, labels=["taste:POSITIVE"])
            for r in ("simulated-reviewer-a", "simulated-reviewer-b")
        ],
    )
    values.update(changes)
    return ReviewRecord(**values)


@pytest.fixture
def records():
    return [
        record(),
        record("t1", "test", text="My bag arrived open although the bowl stayed shut"),
    ]


@pytest.fixture
def frozen(tmp_path, monkeypatch, records):
    monkeypatch.setattr(dataset, "candidate_fingerprint", lambda *args: "a" * 64)
    monkeypatch.setattr(review_runner, "candidate_fingerprint", lambda *args: "a" * 64)
    path = tmp_path / "records.jsonl"
    path.write_text("\n".join(r.model_dump_json() for r in records))
    manifest = freeze(
        path,
        [],
        dataset_id="unit-fixture",
        version="v1",
        protocol=Protocol(bootstrap_samples=100, min_groups_per_split=1),
        model_dir=tmp_path,
    )
    manifest_path = tmp_path / "frozen.json"
    manifest_path.write_text(manifest.model_dump_json())
    return path, manifest_path, manifest


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "synthetic"},
        {"pii_reviewed": False},
        {"independent_of_training": False},
        {"annotations": []},
        {"author": "simulated-reviewer-a"},
    ],
)
def test_unreviewed_or_synthetic_inputs_cannot_pass_audit(records, changes):
    records[0] = record(**changes)
    assert not audit(records, [])["eligible"]


def test_disagreement_requires_independent_adjudicator(records):
    records[0].annotations[1].labels = ["taste:NEGATIVE"]
    result = audit(records, [])
    assert not result["eligible"] and result["annotation_disagreements"] == 1
    records[0].adjudication = Adjudication(
        method="human",
        reviewer="simulated-adjudicator",
        labels=["taste:POSITIVE"],
        reason="Unit test resolution",
    )
    assert audit(records, [])["eligible"]
    records[0].adjudication.reviewer = records[0].author
    assert not audit(records, [])["eligible"]


def test_duplicate_reviewer_is_not_independent(records):
    records[0].annotations[1].reviewer = records[0].annotations[0].reviewer
    assert not audit(records, [])["eligible"]


def test_invalid_labels_and_empty_inputs_are_rejected(tmp_path):
    with pytest.raises(ValidationError):
        Annotation(method="human", reviewer="unit", labels=["invented:POSITIVE"])
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    with pytest.raises(ValueError, match="empty"):
        load_records(path)


def test_input_validation_does_not_echo_sensitive_text(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"private_text": "do not echo this"}')
    with pytest.raises(ValueError) as exc:
        load_records(path)
    assert "do not echo" not in str(exc.value)


def test_known_templates_and_reference_overlap_are_rejected(records):
    records[0].text = "The unusual green pancake tasted amazing, just like home."
    assert not audit(records, [])["eligible"]
    records[0].text = "Ａ new delivery arrived yesterday with the tiny bag still open!"
    refs = [
        {
            "id": "training:1",
            "text": "a new delivery arrived yesterday with the tiny bag still open",
        }
    ]
    assert not audit(records, refs)["eligible"]
    assert OverlapIndex(refs).matches(
        "a new delivery arrived yesterday with the tiny bag still open today"
    )


def test_group_and_text_cannot_cross_splits(records):
    records[1].group_id = records[0].group_id
    assert not audit(records, [])["eligible"]
    records[1].group_id = "other"
    records[1].text = records[0].text.upper()
    assert not audit(records, [])["eligible"]


def test_empty_split_or_duplicate_id_cannot_freeze(records):
    records[1].split = "val"
    assert not audit(records, [])["eligible"]
    records[1].split = "test"
    records[1].id = records[0].id
    assert not audit(records, [])["eligible"]


def test_freeze_requires_provider_permission(frozen, tmp_path):
    path, _, _ = frozen
    with pytest.raises(ValueError, match="provider-use"):
        freeze(
            path,
            [],
            dataset_id="unit-fixture",
            version="v2",
            protocol=Protocol(backend="llm", min_groups_per_split=1),
            model_dir=tmp_path,
        )


def test_frozen_records_references_and_candidate_cannot_drift(frozen, tmp_path, monkeypatch):
    path, _, manifest = frozen
    assert len(review_runner.preflight(path, manifest, [], tmp_path, "val", False)) == 1
    with pytest.raises(ValueError, match="reference"):
        review_runner.preflight(path, manifest, [{"text": "changed"}], tmp_path, "val", False)
    monkeypatch.setattr(review_runner, "candidate_fingerprint", lambda *args: "b" * 64)
    with pytest.raises(ValueError, match="model"):
        review_runner.preflight(path, manifest, [], tmp_path, "val", False)
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="records changed"):
        review_runner.preflight(path, manifest, [], tmp_path, "val", False)


async def test_val_excludes_test_and_failed_final_access_is_retained(frozen, tmp_path, monkeypatch):
    path, manifest_path, _ = frozen
    seen = []

    async def execute(rows, *_):
        seen.extend(r.id for r in rows)
        return {"onnx": [{"status": "error", "labels": [], "id": r.id} for r in rows]}, {}

    monkeypatch.setattr(review_runner, "execute", execute)
    report = await review_runner.run(path, manifest_path, [], model_dir=tmp_path)
    assert seen == ["v1"] and report["status"] == "error"
    assert report["results"]["onnx"]["overall"]["metrics"] is None
    with pytest.raises(ValueError, match="release-test"):
        await review_runner.run(path, manifest_path, [], model_dir=tmp_path, split="test")
    kwargs = dict(
        model_dir=tmp_path, split="test", release_test=True, ledger_dir=tmp_path / "ledger"
    )
    report = await review_runner.run(path, manifest_path, [], **kwargs)
    assert seen == ["v1", "t1"] and ledger_exists(report["test_access_record"])
    with pytest.raises(FileExistsError):
        await review_runner.run(path, manifest_path, [], **kwargs)
    assert seen == ["v1", "t1"]


def test_metrics_are_paired_reproducible_and_sliced(records):
    predictions = [{"status": "ok", "labels": r.resolved_labels()} for r in records]
    options = dict(repeats=100, seed=3)
    result = report_metrics(records, predictions, **options)
    assert result == report_metrics(records, predictions, **options)
    assert result["overall"]["metrics"]["macro_f1"] == 1
    assert result["language"]["en"]["n"] == 2
    assert result["category"]["unseen_phrasing"]["n"] == 2
    assert result["overall"]["warning"]
    delta = paired_delta(records, predictions, predictions, **options)
    assert all(v == [0, 0] for v in delta["ci95"].values())
    predictions[0]["status"] = "error"
    assert not paired_delta(records, predictions, predictions, **options)["available"]
    assert report_metrics(records, predictions, **options)["overall"]["metrics"] is None


def test_one_source_group_does_not_invent_confidence_interval(records):
    records[1].group_id = records[0].group_id
    predictions = [{"status": "ok", "labels": r.resolved_labels()} for r in records]
    assert report_metrics(records, predictions, repeats=100, seed=3)["overall"]["ci95"] is None


async def test_provider_failure_and_timeout_are_errors_without_payload_leak(records, tmp_path):
    async def broken(_):
        raise RuntimeError("secret provider payload")

    results, usage = await review_runner.execute(
        records, Protocol(backend="llm"), tmp_path, predictor=broken
    )
    assert all(p["status"] == "error" for p in results["llm"])
    assert "secret" not in json.dumps(results)
    assert usage["estimated_cost_usd"] is None
    assert review_runner.active_telemetry.get() is None


async def test_llm_receives_inputs_without_annotations_or_source_metadata(monkeypatch):
    from dosadash_ai import llm
    from dosadash_shared import AspectLabel, ReviewScoreBatch, ReviewScoreDraft

    async def fake(**kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        assert set(payload["reviews"][0]) == {"review_id", "rating", "text"}
        assert "simulated-reviewer" not in json.dumps(kwargs["messages"])
        return ReviewScoreBatch(
            scores=[
                ReviewScoreDraft(
                    review_id=1,
                    sentiment="POSITIVE",
                    aspects=[AspectLabel(aspect="taste", sentiment="POSITIVE")],
                )
            ]
        ), "mock"

    monkeypatch.setattr(llm, "structured_completion", fake)
    prediction = await review_runner.llm_prediction(record())
    assert prediction["labels"] == ["taste:POSITIVE"]


def test_fingerprints_are_order_independent_for_objects():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def ledger_exists(path):
    return Path(path).exists()


def test_default_protocol_requires_thirty_groups_per_split(frozen, tmp_path):
    path, _, _ = frozen
    with pytest.raises(ValueError, match="30 source groups"):
        freeze(
            path,
            [],
            dataset_id="unit-fixture",
            version="v2",
            protocol=Protocol(),
            model_dir=tmp_path,
        )


def test_cli_failed_audit_retains_report_and_never_overwrites(tmp_path, monkeypatch):
    import sys

    from . import __main__ as cli

    source = tmp_path / "records.jsonl"
    source.write_text(record(origin="synthetic").model_dump_json())
    destination = tmp_path / "audit.json"
    monkeypatch.setattr(cli, "load_references", lambda *args: [])
    monkeypatch.setattr(
        sys, "argv", ["benchmark", "audit", "--records", str(source), "--out", str(destination)]
    )
    assert cli.main() == 1
    original = destination.read_text()
    assert not json.loads(original)["eligible"]
    with pytest.raises(SystemExit):
        cli.main()
    assert destination.read_text() == original


async def test_timeout_is_retained_as_error(records, tmp_path, monkeypatch):
    async def timeout(*args, **kwargs):
        args[0].close()
        raise TimeoutError

    async def pending(_):
        return {"status": "ok", "labels": []}

    monkeypatch.setattr(review_runner.asyncio, "wait_for", timeout)
    predictions, _ = await review_runner.execute(
        records, Protocol(backend="llm"), tmp_path, predictor=pending
    )
    assert all(p["error"] == "TimeoutError" for p in predictions["llm"])


async def test_inflight_input_changes_invalidate_the_report(frozen, tmp_path, monkeypatch):
    path, manifest_path, _ = frozen

    def mutate():
        path.write_text(path.read_text() + "\n")

    async def execute(rows, *_):
        mutate()
        return {"onnx": [{"status": "ok", "labels": r.resolved_labels()} for r in rows]}, {}

    monkeypatch.setattr(review_runner, "execute", execute)
    report = await review_runner.run(path, manifest_path, [], model_dir=tmp_path)
    assert report["status"] == "error"
    assert not report["inputs_unchanged"] and report["integrity_errors"]

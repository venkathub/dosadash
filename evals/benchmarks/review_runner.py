"""Run a frozen, independently annotated review benchmark without business DB writes."""

import asyncio
import json
import time
from datetime import UTC, datetime

from dosadash_ai.llm.eval_telemetry import EvalTelemetry, active_case, active_telemetry

from .contracts import FrozenDataset
from .dataset import ROOT, audit, candidate_fingerprint, file_hash, fingerprint, load_records
from .metrics import paired_delta, report_metrics


async def llm_prediction(record):
    from dosadash_ai.llm import structured_completion
    from dosadash_ai.routers.reviews import build_score_messages, sanitize_scores
    from dosadash_shared import (
        REVIEW_SENTIMENT_PROMPT_VERSION,
        ReviewScoreBatch,
        ReviewScoreSourceItem,
    )

    # Only the model input is sent; annotations, categories and source metadata never are.
    requested = [ReviewScoreSourceItem(review_id=1, rating=record.rating, text=record.text)]
    parsed, model = await structured_completion(
        messages=build_score_messages(requested),
        response_model=ReviewScoreBatch,
        trace_name="evals.independent_reviews",
        prompt_version=REVIEW_SENTIMENT_PROMPT_VERSION,
        session_id="evals:independent-reviews",
        max_tokens=1000,
    )
    kept, rejected = sanitize_scores(requested, parsed)
    if len(kept) != 1 or kept[0].review_id != 1:
        raise ValueError("missing or invalid model result")
    return {
        "status": "ok",
        "model": model,
        "labels": sorted(f"{a.aspect}:{a.sentiment}" for a in kept[0].aspects),
        "guardrail_rejections": len(rejected),
    }


def reserve_test(manifest, ledger_dir):
    """Exclusive create prevents accidental repeats/concurrent reveal in this checkout.

    This is an audit guard, not an access-control system: repository owners can
    read the dataset or delete files. Never describe that as cryptographic secrecy.
    """
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / f"{manifest.dataset_id}-{manifest.version}.json"
    with path.open("x") as handle:
        json.dump(
            {
                "dataset": manifest.dataset_id,
                "version": manifest.version,
                "records_sha256": manifest.records_sha256,
                "candidate_sha256": manifest.candidate_sha256,
                "opened_at": datetime.now(UTC).isoformat(),
                "note": "Test access consumed before execution; failures do not reset it.",
            },
            handle,
            indent=2,
        )
    return path


def preflight(records_path, manifest, references, model_dir, split, release_test):
    if file_hash(records_path) != manifest.records_sha256:
        raise ValueError("records changed after freeze")
    if fingerprint(references) != manifest.reference_sha256:
        raise ValueError("overlap reference corpus changed after freeze")
    if candidate_fingerprint(model_dir, manifest.protocol) != manifest.candidate_sha256:
        raise ValueError(
            "model, prompt, routing, protocol or benchmark source changed after freeze"
        )
    records = load_records(records_path)
    if not audit(records, references)["eligible"]:
        raise ValueError("dataset no longer passes independent-data audit")
    if manifest.protocol.backend in {"llm", "compare"} and not all(
        r.provider_use_allowed for r in records
    ):
        raise ValueError("provider-use permission is missing")
    if split == "test" and not release_test:
        raise ValueError("test access requires --release-test after validation selection is frozen")
    return [r for r in records if r.split == split]


async def execute(records, protocol, model_dir, *, predictor=llm_prediction):
    backends = ["onnx", "llm"] if protocol.backend == "compare" else [protocol.backend]
    results = {name: [] for name in backends}
    ledger = EvalTelemetry(
        max_calls=protocol.max_calls,
        max_tokens=protocol.max_tokens,
        max_cost_usd=protocol.max_cost_usd,
    )
    telemetry_token = active_telemetry.set(ledger)
    try:
        champion = None
        load_error = None
        if "onnx" in backends:
            try:
                from dosadash_ml.finetune.dataset import LABELS
                from dosadash_ml.finetune.predict import load_sentiment_champion

                champion = load_sentiment_champion(model_dir)
                if champion.labels != LABELS:
                    raise ValueError("ONNX label registry differs from benchmark labels")
            except Exception as exc:
                champion = None
                load_error = type(exc).__name__
        for record in records:
            token = active_case.set(record.id)
            try:
                for backend in backends:
                    started = time.monotonic()
                    try:
                        if backend == "llm":
                            prediction = await asyncio.wait_for(
                                predictor(record), timeout=protocol.timeout_seconds
                            )
                        else:
                            if champion is None:
                                raise RuntimeError("ONNX load failed: " + str(load_error))
                            from dosadash_ml.finetune.predict import predict_sentiment

                            local = predict_sentiment(champion, [record.text])[0]
                            prediction = {
                                "status": "ok",
                                "labels": list(local.labels),
                                "model": champion.version,
                                "confident": local.confident,
                            }
                    except Exception as exc:
                        # Never leak a provider error body containing source text or credentials.
                        prediction = {"status": "error", "error": type(exc).__name__, "labels": []}
                    prediction.update(
                        id=record.id, latency_ms=round((time.monotonic() - started) * 1000, 2)
                    )
                    results[backend].append(prediction)
            finally:
                active_case.reset(token)
    finally:
        active_telemetry.reset(telemetry_token)
    return results, ledger.summary()


async def run(
    records_path,
    manifest_path,
    references,
    *,
    model_dir,
    split="val",
    release_test=False,
    ledger_dir=None,
):
    manifest = FrozenDataset.model_validate_json(manifest_path.read_text())
    records = preflight(records_path, manifest, references, model_dir, split, release_test)
    access = None
    if split == "test":
        access = reserve_test(manifest, ledger_dir or ROOT / "data/independent/test-access")
    if manifest.protocol.backend in {"llm", "compare"}:
        from dosadash_ai.llm import configure_tracing

        configure_tracing()
    predictions, usage = await execute(records, manifest.protocol, model_dir)
    try:
        inputs_unchanged = (
            file_hash(records_path) == manifest.records_sha256
            and candidate_fingerprint(model_dir, manifest.protocol) == manifest.candidate_sha256
        )
    except Exception:
        inputs_unchanged = False
    options = {"repeats": manifest.protocol.bootstrap_samples, "seed": manifest.protocol.seed}
    complete = inputs_unchanged and all(
        p["status"] == "ok" for rows in predictions.values() for p in rows
    )
    result = {
        "schema_version": 1,
        "status": "complete" if complete else "error",
        "quality_gate": "unconfigured: measurements never automatically promote a model",
        "inputs_unchanged": inputs_unchanged,
        "integrity_errors": [] if inputs_unchanged else ["inputs changed during execution"],
        "dataset_id": manifest.dataset_id,
        "dataset_version": manifest.version,
        "split": split,
        "n": len(records),
        "records_sha256": manifest.records_sha256,
        "reference_sha256": manifest.reference_sha256,
        "candidate_sha256": manifest.candidate_sha256,
        "protocol": manifest.protocol.model_dump(),
        "audit": manifest.audit,
        "ran_at": datetime.now(UTC).isoformat(),
        "test_access_record": str(access) if access else None,
        "results": {
            name: report_metrics(records, rows, **options) for name, rows in predictions.items()
        },
        "predictions": predictions,
        "usage": usage,
        "failures": {
            name: [
                r.id
                for r, p in zip(records, rows, strict=True)
                if p["status"] != "ok" or set(p["labels"]) != set(r.resolved_labels())
            ]
            for name, rows in predictions.items()
        },
        "limitations": [
            "Human provenance attestations cannot be verified automatically.",
            "Overlap checks detect lexical matches, not every semantic or pretraining overlap.",
            "Intervals resample source groups; small slices are descriptive, not a quality gate.",
            "ONNX raw classifier and guarded LLM are compared; hybrid serving is not measured.",
            "LLM sees text and rating as in production; ONNX sees text only.",
            "No invoice, audio or independent ordering benchmark is claimed by this report.",
            "Zero provider calls does not mean CPU hosting and annotation have zero cost.",
        ],
    }
    if manifest.protocol.backend == "compare":
        result["paired_comparison"] = paired_delta(
            records, predictions["onnx"], predictions["llm"], **options
        )
    return result

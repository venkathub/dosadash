"""Run real review/audio/image components or an explicitly labeled scorer replay."""

import asyncio
import base64
import hashlib
import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from random import Random
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dosadash_ai.llm.eval_telemetry import EvalTelemetry, active_case, active_telemetry
from dosadash_ml.finetune.dataset import LABELS
from dosadash_shared import InvoiceExtraction

from .metrics import audio_metrics, invoice_metrics

ROOT = Path(__file__).resolve().parents[2]


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    task: Literal["review", "audio", "invoice"]
    group_id: str = Field(min_length=1)
    language: str = Field(min_length=1)
    category: str = Field(min_length=1)
    origin: Literal["synthetic", "permissioned_real"]
    permission_ref: str = Field(min_length=1)
    provider_use_allowed: bool = False
    pii_reviewed: bool = False
    source_ref: str | None = None
    expected: str | dict | list[str]
    path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    mime_type: str | None = None
    text: str | None = Field(default=None, max_length=2000)
    rating: int = Field(default=3, ge=1, le=5)
    required_terms: list[str] = Field(default_factory=list)
    generator: str | None = None

    @model_validator(mode="after")
    def valid_case(self):
        if self.origin == "permissioned_real" and (not self.pii_reviewed or not self.source_ref):
            raise ValueError("real fixtures require source provenance and privacy review")
        if self.task == "review":
            if not self.text or not isinstance(self.expected, list) or not self.expected:
                raise ValueError("review text and aspect labels required")
            if set(self.expected) - set(LABELS):
                raise ValueError("unregistered review label")
        else:
            if not self.path or not self.sha256:
                raise ValueError("asset path and frozen hash required")
            if self.task == "audio":
                if not isinstance(self.expected, str) or not self.expected.strip():
                    raise ValueError("reference transcript required")
                if self.mime_type not in {
                    "audio/wav",
                    "audio/ogg",
                    "audio/mpeg",
                    "audio/mp4",
                    "audio/webm",
                }:
                    raise ValueError("unsupported audio MIME")
            else:
                InvoiceExtraction.model_validate(self.expected)
                if self.mime_type not in {"image/png", "image/jpeg", "image/webp"}:
                    raise ValueError("unsupported image MIME")
        return self


def read_cases(manifest):
    data = json.loads(manifest.read_text())
    cases = [Case.model_validate(c) for c in data["cases"]]
    if not cases or len(cases) > 100 or len({c.id for c in cases}) != len(cases):
        raise ValueError("require 1–100 uniquely identified cases")
    return cases


def asset(case, manifest):
    path = (manifest.parent / case.path).resolve()
    if not path.is_relative_to(manifest.parent.resolve()):
        raise ValueError("asset path escapes dataset directory")
    raw = path.read_bytes()
    maximum = 3_000_000 if case.task == "audio" else 7_000_000
    if len(raw) > maximum or hashlib.sha256(raw).hexdigest() != case.sha256:
        raise ValueError("asset too large or changed since manifest creation")
    return raw


def fingerprint(manifest):
    from dosadash_ai.config import get_settings
    from dosadash_ai.invoice.extract import VISION_MODELS

    paths = [
        manifest,
        *sorted((ROOT / "evals/portfolio").glob("*.py")),
        *sorted((ROOT / "apps/ai/src/dosadash_ai/llm").glob("*.py")),
        *sorted((ROOT / "apps/ai/src/dosadash_ai/invoice").glob("*.py")),
        ROOT / "apps/ai/src/dosadash_ai/routers/reviews.py",
        ROOT / "apps/ai/src/dosadash_ai/redaction.py",
        ROOT / "uv.lock",
        ROOT / "apps/ai/prompts/invoice_extract_v1.md",
        ROOT / "apps/ai/prompts/review_sentiment_v1.md",
        *sorted((ROOT / "packages/ml/artifacts/sentiment/onnx").glob("*")),
        *sorted((ROOT / "packages/ml/src/dosadash_ml/finetune").glob("*.py")),
        ROOT / "packages/shared/src/dosadash_shared/reviews.py",
        ROOT / "packages/shared/src/dosadash_shared/invoice.py",
    ]
    settings = get_settings()
    values = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    values += [str(settings.llm_models), settings.stt_model, str(VISION_MODELS)]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


async def predict(case, manifest):
    if case.task == "audio":
        from dosadash_ai.llm.stt import transcribe
        from dosadash_shared import SttIn

        result = await transcribe(
            SttIn(
                audio_base64=base64.b64encode(asset(case, manifest)).decode(),
                mime_type=case.mime_type,
                session_id=f"portfolio:{case.id}",
            )
        )
        return result.transcript, result.model
    if case.task == "invoice":
        from dosadash_ai.invoice.extract import extract_invoice
        from dosadash_shared import InvoiceExtractIn

        result = await extract_invoice(
            InvoiceExtractIn(
                image_base64=base64.b64encode(asset(case, manifest)).decode(),
                mime_type=case.mime_type,
                session_id=f"portfolio:{case.id}",
            )
        )
        if result.error or result.extraction is None:
            raise RuntimeError("invoice provider did not return an extraction")
        return result.extraction.model_dump(mode="json"), result.model
    from evals.benchmarks.review_runner import llm_prediction

    result = await llm_prediction(SimpleNamespace(text=case.text, rating=case.rating))
    return result["labels"], result["model"]


def metrics(case, prediction):
    if case.task == "audio":
        return audio_metrics(case.expected, prediction, case.required_terms)
    if case.task == "invoice":
        return invoice_metrics(case.expected, prediction)
    a, b = set(case.expected), set(prediction)
    return {"label_f1": 2 * len(a & b) / (len(a) + len(b)), "exact_match": a == b}


def aggregate(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["group_id"]].append(row)
    result = {
        "n": len(rows),
        "groups": len(groups),
        "metrics": None,
        "warning": "Small artificial/regression sample; no generalization claim.",
    }
    if any(r["status"] != "ok" for r in rows):
        result["warning"] = "Incomplete predictions; aggregate quality unavailable."
        return result
    names = [k for k, v in rows[0]["metrics"].items() if v is not None]
    result["metrics"] = {k: sum(r["metrics"][k] for r in rows) / len(rows) for k in names}
    # Descriptive cluster intervals: variants of one invoice do not count as three sources.
    result["ci95"] = None
    if len(groups) >= 2:
        rng = Random(42)
        draws = [{k: 0.0 for k in names} for _ in range(500)]
        for draw in draws:
            sampled = [
                r for group in rng.choices(list(groups.values()), k=len(groups)) for r in group
            ]
            for k in names:
                draw[k] = sum(r["metrics"][k] for r in sampled) / len(sampled)
        result["ci95"] = {
            k: [sorted(d[k] for d in draws)[12], sorted(d[k] for d in draws)[487]] for k in names
        }
    return result


async def run(manifest, mode, tasks=None):
    cases = read_cases(manifest)
    if tasks:
        cases = [case for case in cases if case.task in tasks]
    if not cases:
        raise ValueError("no cases selected")
    for case in cases:
        if case.path:
            asset(case, manifest)
    if mode == "live" and not all(c.provider_use_allowed for c in cases):
        raise ValueError("provider permission missing")
    before = fingerprint(manifest)
    ledger = EvalTelemetry(max_calls=300, max_tokens=200_000, max_cost_usd=1.0)
    token = active_telemetry.set(ledger)
    if mode == "live":
        from dosadash_ai.llm import configure_tracing

        configure_tracing()
    rows = []
    audio_attempts = 0
    try:
        for case in cases:
            started = time.monotonic()
            case_token = active_case.set(case.id)
            row = {
                "id": case.id,
                "task": case.task,
                "group_id": case.group_id,
                "language": case.language,
                "category": case.category,
            }
            try:
                if mode == "replay":
                    prediction, model = case.expected, "reference-replay:NO-MODEL-INFERENCE"
                elif mode == "local":
                    if case.task != "review":
                        row.update(status="not_run", reason="requires remote inference")
                        rows.append(row)
                        continue
                    from dosadash_ml.finetune.predict import (
                        load_sentiment_champion,
                        predict_sentiment,
                    )

                    champion = load_sentiment_champion(ROOT / "packages/ml/artifacts")
                    prediction, model = (
                        list(predict_sentiment(champion, [case.text])[0].labels),
                        champion.version,
                    )
                else:
                    if case.task == "audio":
                        audio_attempts += 1
                    prediction, model = await asyncio.wait_for(predict(case, manifest), timeout=120)
                row.update(
                    status="ok",
                    model=model,
                    metrics=metrics(case, prediction),
                    prediction=prediction,
                )
            except Exception as exc:
                row.update(status="error", error=type(exc).__name__)
            finally:
                active_case.reset(case_token)
            row["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
            rows.append(row)
    finally:
        active_telemetry.reset(token)
    try:
        unchanged = fingerprint(manifest) == before
    except OSError:
        unchanged = False
    for case in cases:
        if case.path:
            try:
                asset(case, manifest)
            except (OSError, ValueError):
                unchanged = False
    results = {}
    for task in sorted({r["task"] for r in rows}):
        selected = [r for r in rows if r["task"] == task]
        results[task] = {"overall": aggregate(selected)}
        for dim in ("language", "category"):
            results[task][dim] = {
                key: aggregate([r for r in selected if r[dim] == key])
                for key in sorted({r[dim] for r in selected})
            }
    return {
        "schema_version": 1,
        "ran_at": datetime.now(UTC).isoformat(),
        "status": "complete"
        if unchanged and all(r["status"] == "ok" for r in rows)
        else "partial"
        if mode == "local" and unchanged
        else "error",
        "evidence": "synthetic_portfolio_demo"
        if all(c.origin == "synthetic" for c in cases)
        else "permissioned_regression_not_independent",
        "mode": mode,
        "tasks": sorted({c.task for c in cases}),
        "fingerprint": before,
        "inputs_unchanged": unchanged,
        "results": results,
        "cases": rows,
        "usage": ledger.summary(),
        "audio_attempts": audio_attempts,
        "audio_cost_usd": None,
        "limitations": [
            "Replay validates the scorer only; it does not run a model.",
            "Local mode runs the real ONNX classifier; audio/invoices are not run.",
            "Synthetic TTS and rendered invoices are not real-world audio or invoice evidence.",
            "Command-term recall measures the transcript, not downstream order execution.",
            "Audio cost is unmeasured; the $1 known-cost limit applies only to text/vision calls.",
            "Small-group intervals are descriptive; no blocking quality threshold.",
        ],
    }

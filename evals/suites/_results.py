"""Shared live evaluation contracts, execution and provenance (no provider calls on import)."""

import asyncio
import hashlib
import json
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.engine import make_url

from dosadash_ai.llm.eval_telemetry import active_case

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "evals/config.json"


class SuiteResult(BaseModel):
    name: str
    status: Literal["pass", "fail", "error", "skipped"]
    blocking: bool = False
    calibrated: bool = False
    reason: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    cases: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: float = 0

    @property
    def gate_failed(self) -> bool:
        # A selected suite that never executed is NOT a successful measurement.
        return self.status in {"error", "skipped"} or (
            self.status == "fail" and (self.blocking or self.metrics.get("bypasses", 0) > 0)
        )


def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text())
    if config["schema_version"] != 1:
        raise ValueError("unsupported evaluation config")
    for name, spec in config["suites"].items():
        if spec["blocking"] and not spec["calibrated"]:
            raise ValueError(f"{name}: blocking quality gate requires calibration")
    return config


def judge_models() -> list[str]:
    models = load_config()["judges"]["models"]
    if not models or not all(isinstance(model, str) and model.strip() for model in models):
        raise ValueError("judge model chain must contain nonempty model names")
    return models


def require_eval_database() -> None:
    """Live harnesses mutate fixtures: only local, explicitly named evaluation DBs."""
    from dosadash_ai.config import get_settings

    url = os.environ.get("EVAL_DATABASE_URL", "")
    if not url:
        raise ValueError("EVAL_DATABASE_URL is required; use a dedicated local *_eval database")
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1", "::1", "postgres"} or not (
        parsed.database or ""
    ).endswith("_eval"):
        raise ValueError("live suites require a local database with a name ending in _eval")
    for key in ("AI_DATABASE_URL", "API_DATABASE_URL"):
        if os.environ.get(key) != url:
            raise ValueError(f"{key} must equal EVAL_DATABASE_URL")
    settings = get_settings()
    if settings.database_url != url:
        raise ValueError("effective AI database configuration differs from evaluation database")
    if settings.copilot_database_url:
        other = make_url(settings.copilot_database_url)
        if (other.host, other.port, other.database) != (parsed.host, parsed.port, parsed.database):
            raise ValueError("copilot database must use the same evaluation database")
    # Set the actual cached settings object too; test imports may have loaded it earlier.
    settings.semcache_enabled = False


def load_jsonl(name: str) -> list[dict]:
    cases = [
        json.loads(line)
        for line in (ROOT / "evals/golden" / name).read_text().splitlines()
        if line.strip()
    ]
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError(f"empty dataset or duplicate ids: {name}")
    return cases


async def evaluate_cases(
    name: str,
    cases: list[dict],
    execute: Callable[[dict], Awaitable[dict]],
    *,
    case_timeout: float = 120,
) -> SuiteResult:
    spec = load_config()["suites"][name]
    started = time.monotonic()
    reports = []
    for case in cases:
        report = {
            "id": f"{name}:{case['id']}",
            "language": case.get("language", "en"),
            "tags": case.get("tags", []),
        }
        case_token = active_case.set(report["id"])
        try:
            output = await asyncio.wait_for(execute(case), timeout=case_timeout)
            report.update(output)
            report["status"] = (
                "fail" if output.get("problems") or output.get("bypasses") else "pass"
            )
        except Exception as exc:  # noqa: BLE001 — each failure is evidence; continue other cases
            report.update(status="error", error=type(exc).__name__)
            if hasattr(exc, "eval_detail"):
                report["error_detail"] = exc.eval_detail
        finally:
            active_case.reset(case_token)
        reports.append(report)
        if len(reports) % 25 == 0:
            print(f"{name}: executed {len(reports)}/{len(cases)} cases", flush=True)
    errors = sum(c["status"] == "error" for c in reports)
    passed = sum(c["status"] == "pass" for c in reports)
    bypasses = sum(len(c.get("bypasses", [])) for c in reports)
    rate = passed / len(reports) if reports else 0
    status = (
        "error"
        if errors or not reports
        else ("pass" if rate >= spec["threshold"] and not bypasses else "fail")
    )
    metrics = {"pass_rate": rate, "errors": float(errors), "bypasses": float(bypasses)}
    for language in sorted({c["language"] for c in reports}):
        selected = [c for c in reports if c["language"] == language]
        metrics[f"pass_rate_{language}"] = sum(c["status"] == "pass" for c in selected) / len(
            selected
        )
    return SuiteResult(
        name=name,
        status=status,
        blocking=spec["blocking"],
        calibrated=spec["calibrated"],
        metrics=metrics,
        cases=reports,
        reason="empty required suite" if not reports else None,
        duration_ms=round((time.monotonic() - started) * 1000, 2),
    )


def provenance(profile: str) -> dict:
    files = sorted(
        [
            *ROOT.glob("evals/golden/*.jsonl"),
            *ROOT.glob("evals/rubrics/*.md"),
            *ROOT.glob("apps/ai/prompts/*.md"),
            *ROOT.glob("knowledge/**/*.md"),
            CONFIG_PATH,
        ]
    )
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    sources = sorted(
        [
            *ROOT.glob("apps/*/src/**/*.py"),
            *ROOT.glob("packages/*/src/**/*.py"),
            *ROOT.glob("evals/**/*.py"),
            *ROOT.glob(".github/workflows/*.yml"),
        ]
    )
    source_digest = hashlib.sha256()
    for path in sources:
        source_digest.update(str(path.relative_to(ROOT)).encode())
        source_digest.update(path.read_bytes())
    return {
        "source_sha256": source_digest.hexdigest(),
        "fixture_recipe": {"users": 40, "days": 365, "seed": 42, "end_date": "2026-08-20"},
        "schema_version": 1,
        "profile": profile,
        "git_sha": commit,
        "asset_sha256": hashes,
        "cache": "disabled",
        "thresholds": load_config()["suites"],
    }

"""Key-free CI gates for the analytics copilot (Phase 5, Hard Rule 5).

Pins the SQL guardrail against the adversarial golden set (zero false
accepts, zero false rejects), and keeps the prompt / guardrail / DB-role
allowlists from drifting apart.
"""

import json
import re
from pathlib import Path

from dosadash_ai.copilot.guardrail import ALLOWED_TABLES, SqlValidationError, validate_sql

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "copilot_guardrail.jsonl"
PROMPT = Path(__file__).resolve().parents[2] / "apps" / "ai" / "prompts" / "copilot_v1.md"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "apps/api/migrations/versions/b3f9c82d4e61_readonly_copilot_role.py"
)


def _cases() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def test_golden_set_shape():
    cases = _cases()
    assert len(cases) >= 25
    assert len({c["id"] for c in cases}) == len(cases)
    rejects = [c for c in cases if c["expect"] == "reject"]
    assert len(rejects) >= 15  # adversarial coverage floor


def test_guardrail_zero_false_accepts():
    failures = []
    for case in _cases():
        if case["expect"] != "reject":
            continue
        try:
            validate_sql(case["sql"])
            failures.append(f"{case['id']} ({case.get('reason')}): ACCEPTED")
        except SqlValidationError:
            pass
    assert not failures, f"guardrail bypasses: {failures}"


def test_guardrail_zero_false_rejects():
    failures = []
    for case in _cases():
        if case["expect"] != "accept":
            continue
        try:
            sql = validate_sql(case["sql"])
            assert "limit" in sql.lower()  # LIMIT always enforced
        except SqlValidationError as exc:
            failures.append(f"{case['id']}: rejected ({exc})")
    assert not failures, f"legitimate queries rejected: {failures}"


def test_prompt_matches_guardrail_allowlist():
    prompt = PROMPT.read_text()
    for table in ALLOWED_TABLES:
        assert re.search(rf"\b{table}\(", prompt), f"prompt missing schema for {table}"
    assert "phone" in prompt and "FORBIDDEN" in prompt  # PII rule stated to the model


def test_db_role_grants_match_guardrail_allowlist():
    migration = MIGRATION.read_text()
    granted = set(re.findall(r'"(\w+)",', migration.split("COPILOT_TABLES")[1].split("]")[0]))
    assert granted == set(ALLOWED_TABLES), (
        f"migration grants {granted ^ set(ALLOWED_TABLES)} out of sync with guardrail"
    )

"""Key-free asset gates for the Phase 14 lifecycle sync (slice 1).

The webhook and the reconciler translate GitHub truth (labels, comment
markers, branch names) into local state. Those translation contracts live
in dosadash_shared — these gates pin them to the workflow files and the
label registry so neither side can drift alone:
- every status-bearing ai:* label appears exactly once in the reconciler's
  precedence table, and the precedence ordering is the loop's actual
  causality (verified > fixed > decisions > triage verdicts);
- the RCA / Prod-verification comment markers the webhook keys on are the
  byte-identical strings the workflows are instructed to write;
- the fix-branch naming contract (`fix/issue-N`) the webhook uses to map
  PRs back to issues is what the fixer workflow actually instructs.
"""

import re
from pathlib import Path

from dosadash_shared import (
    FIX_BRANCH_PREFIX,
    GITHUB_LABELS,
    LABEL_AI_APPROVED,
    LABEL_AI_AUTO_FIX,
    LABEL_AI_FIXED,
    LABEL_AI_NEEDS_APPROVAL,
    LABEL_AI_REJECTED,
    LABEL_AI_VERIFIED,
    LABEL_STATUS_PRECEDENCE,
    RCA_COMMENT_MARKER,
    VERIFICATION_COMMENT_MARKER,
    FeedbackStatus,
)

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
FIX_WORKFLOW = _WORKFLOWS / "claude-issue-fix.yml"
VERIFY_WORKFLOW = _WORKFLOWS / "claude-fix-verify.yml"
REVIEW_WORKFLOW = _WORKFLOWS / "claude-pr-review.yml"
SPEC_WORKFLOW = _WORKFLOWS / "claude-issue-spec.yml"


def test_every_status_bearing_label_in_precedence_exactly_once() -> None:
    precedence_labels = [label for label, _ in LABEL_STATUS_PRECEDENCE]
    status_bearing = {
        LABEL_AI_AUTO_FIX,
        LABEL_AI_NEEDS_APPROVAL,
        LABEL_AI_APPROVED,
        LABEL_AI_REJECTED,
        LABEL_AI_FIXED,
        LABEL_AI_VERIFIED,
    }
    assert set(precedence_labels) == status_bearing
    assert len(precedence_labels) == len(set(precedence_labels)), "duplicate precedence entry"
    # and none of them can reference a label the registry doesn't define
    assert status_bearing <= set(GITHUB_LABELS)


def test_precedence_ordering_is_loop_causality() -> None:
    order = [label for label, _ in LABEL_STATUS_PRECEDENCE]
    assert order.index(LABEL_AI_VERIFIED) < order.index(LABEL_AI_FIXED)
    assert order.index(LABEL_AI_FIXED) < order.index(LABEL_AI_APPROVED)
    assert order.index(LABEL_AI_APPROVED) < order.index(LABEL_AI_NEEDS_APPROVAL)
    assert order.index(LABEL_AI_NEEDS_APPROVAL) < order.index(LABEL_AI_AUTO_FIX)


def test_precedence_statuses_are_valid() -> None:
    for _, status in LABEL_STATUS_PRECEDENCE:
        assert isinstance(status, FeedbackStatus)


def test_rca_marker_matches_fixer_workflow() -> None:
    assert RCA_COMMENT_MARKER in FIX_WORKFLOW.read_text(), (
        "the webhook keys RCA_POSTED on this marker — if the fixer prompt "
        "changes its comment title, change dosadash_shared.RCA_COMMENT_MARKER with it"
    )


def test_verification_marker_matches_verifier_workflow() -> None:
    assert VERIFICATION_COMMENT_MARKER in VERIFY_WORKFLOW.read_text(), (
        "the webhook keys VERIFICATION_POSTED on this marker — if the verifier "
        "prompt changes its comment title, change VERIFICATION_COMMENT_MARKER with it"
    )


def test_fix_branch_contract_matches_fixer_workflow() -> None:
    # The webhook + reconciler map PRs → issues via `fix/issue-N`.
    assert FIX_BRANCH_PREFIX in FIX_WORKFLOW.read_text(), (
        "the fixer must be instructed to branch as fix/issue-N — the PR→issue "
        "mapping in the webhook and GitHubClient.find_fix_pr depend on it"
    )


# ---------------------------------------------------- run ingest (slice 3)
# Both workflows self-report their outcome to the api. These gates keep
# the ingest steps honest: present, best-effort, and with the reported
# model literal equal to the --model pin (so metrics can never silently
# lie about which model ran).


def _model_pin(text: str) -> str:
    match = re.search(r"--model (claude-[a-z0-9.-]+)", text)
    assert match is not None
    return match.group(1)


def _ingest_model(text: str) -> str:
    match = re.search(r'"model":"(claude-[a-z0-9.-]+)"', text)
    assert match is not None, "ingest step must report the model it ran"
    return match.group(1)


def test_fix_workflow_reports_runs_best_effort() -> None:
    text = FIX_WORKFLOW.read_text()
    assert "FIXER_INGEST_URL" in text and "X-Internal-Token" in text
    assert '"workflow":"fix"' in text
    assert "if: always()" in text, "a FAILED run is exactly the one worth reporting"
    assert "non-blocking" in text  # curl failure must never fail the fix run
    assert "secrets.FIXER_INGEST_URL" in text  # never a hardcoded URL


def test_ingest_carries_cache_telemetry() -> None:
    """Phase 15 S7: both workflows parse the action's execution file for
    cache/cost usage — and DEGRADE to the base payload on any parse
    failure (telemetry must never break outcome reporting, and outcome
    reporting must never break the run)."""
    for workflow in (FIX_WORKFLOW, VERIFY_WORKFLOW, REVIEW_WORKFLOW, SPEC_WORKFLOW):
        text = workflow.read_text()
        assert "steps.claude.outputs.execution_file" in text, (
            f"{workflow.name}: ingest must read the action's execution file"
        )
        for field in (
            "cost_usd",
            "cache_read_tokens",
            "cache_creation_tokens",
            "input_tokens",
            "output_tokens",
        ):
            assert field in text, f"{workflow.name}: ingest must extract {field}"
        assert 'PAYLOAD="$BASE"' in text, (
            f"{workflow.name}: a jq failure must degrade to the base payload"
        )
        assert "USAGE='{}'" in text, (
            f"{workflow.name}: a missing/unreadable execution file must degrade to no usage"
        )


def test_verify_workflow_reports_runs_conditionally() -> None:
    text = VERIFY_WORKFLOW.read_text()
    assert "FIXER_INGEST_URL" in text and "X-Internal-Token" in text
    assert '"workflow":"verify"' in text
    # empty queues spent zero tokens — they must also produce zero rows
    assert "steps.pending.outputs.numbers != ''" in text


def test_ingest_model_matches_pin() -> None:
    for workflow in (FIX_WORKFLOW, VERIFY_WORKFLOW, REVIEW_WORKFLOW, SPEC_WORKFLOW):
        text = workflow.read_text()
        assert _ingest_model(text) == _model_pin(text), (
            f"{workflow.name}: ingest reports a different model than --model pins — "
            "update both together"
        )


def test_spec_marker_matches_spec_workflow() -> None:
    """Phase 15 S2: the webhook's SPEC_POSTED mapping and the spec
    workflow must agree on the comment marker byte-for-byte."""
    from dosadash_shared import SPEC_COMMENT_MARKER

    assert SPEC_COMMENT_MARKER == "## Spec"
    assert SPEC_COMMENT_MARKER in SPEC_WORKFLOW.read_text()

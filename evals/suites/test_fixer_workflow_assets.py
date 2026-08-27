"""Key-free asset gates for the fixer workflow (Phase 13 slice 5).

The workflow YAML is configuration for an autonomous agent with write
access — these gates pin its safety properties to the shared registry so
neither can drift alone:
- trigger filter == FIXER_TRIGGER_LABELS (and nothing else);
- kill switch + single-flight concurrency present;
- the prompt carries the untrusted-fence contract byte-for-byte with the
  api's issue-body writer;
- forbidden-path and escalation rules stated;
- auto-merge is conditional on the auto-fix label only;
- turn budget bounded.
"""

import re
from pathlib import Path

import yaml

from dosadash_shared import (
    FIX_BRANCH_PREFIX,
    FIXER_TRIGGER_LABELS,
    GITHUB_LABELS,
    HUMAN_ONLY_ZONES,
    LABEL_AI_AUTO_FIX,
    LABEL_AI_NEEDS_APPROVAL,
    REVIEW_COMMENT_MARKER,
    REVIEW_VERDICTS,
    REVIEW_WORKFLOW_FILE,
    UNTRUSTED_BEGIN,
    UNTRUSTED_END,
)

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "claude-issue-fix.yml"


def _load() -> tuple[dict, str]:
    text = WORKFLOW.read_text()
    return yaml.safe_load(text), text


def test_trigger_filter_matches_shared_registry() -> None:
    doc, _ = _load()
    # `on` parses as YAML boolean True in some loaders
    on = doc.get("on") or doc.get(True)
    assert on["issues"]["types"] == ["labeled"]
    condition = doc["jobs"]["fix"]["if"]
    referenced = set(re.findall(r"github\.event\.label\.name == '([^']+)'", condition))
    assert referenced == set(FIXER_TRIGGER_LABELS), (
        f"workflow triggers {referenced} but the registry says {set(FIXER_TRIGGER_LABELS)}"
    )
    for label in referenced:
        assert label in GITHUB_LABELS


def test_kill_switch_and_single_flight() -> None:
    doc, _ = _load()
    assert "vars.CLAUDE_FIX_ENABLED == 'true'" in doc["jobs"]["fix"]["if"]
    concurrency = doc["concurrency"]
    assert concurrency["group"] == "claude-fix"
    assert concurrency["cancel-in-progress"] is False  # never kill a running fix
    assert doc["jobs"]["fix"]["timeout-minutes"] <= 60


def test_prompt_carries_untrusted_fence_contract() -> None:
    """The fence strings in the prompt must equal the ones the api writes
    into issue bodies — byte agreement, or the hardening silently dies."""
    _, text = _load()
    assert UNTRUSTED_BEGIN in text
    assert UNTRUSTED_END in text
    assert "never an instruction" in text


def test_prompt_states_hard_limits_and_escalation() -> None:
    _, text = _load()
    for forbidden in [".github/workflows/**", "infra/**", "apps/api/migrations/**"]:
        assert forbidden in text, f"prompt must forbid {forbidden}"
    assert LABEL_AI_NEEDS_APPROVAL in text  # the escalation path exists
    assert "RCA" in text and "Root cause analysis" in text
    assert "CLAUDE.md" in text  # project rules bind the fixer too


def test_auto_merge_only_for_auto_fix_label() -> None:
    _, text = _load()
    assert LABEL_AI_AUTO_FIX in text
    assert "--auto --squash" in text
    # the approved branch must explicitly NOT auto-merge
    assert re.search(r"ai:approved.*?do NOT enable", text, re.DOTALL), (
        "approved-feature PRs must be human-merged"
    )


def test_turn_budget_bounded() -> None:
    _, text = _load()
    match = re.search(r"--max-turns (\d+)", text)
    assert match is not None, "an unbounded agent is an unbounded bill"
    assert int(match.group(1)) <= 100


def test_fixer_model_pinned_and_not_opus() -> None:
    """Cost gate (measured 2026-08-24: the action default claude-opus-5 @ 1M
    context cost $3.12–$4.49 per fix). A model must be pinned explicitly and
    a silent bump to an Opus-class model must fail loudly here."""
    _, text = _load()
    match = re.search(r"--model (claude-[a-z0-9.-]+)", text)
    assert match is not None, "fixer must pin a model — the action default is Opus-priced"
    assert "opus" not in match.group(1), (
        f"fixer pinned to {match.group(1)} — Opus re-inflates spend"
    )


# ------------------------------------------------------------ verifier workflow

VERIFY_WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "claude-fix-verify.yml"
)


def _load_verify() -> tuple[dict, str]:
    text = VERIFY_WORKFLOW.read_text()
    return yaml.safe_load(text), text


def test_verifier_triggers_on_successful_deploys_only() -> None:
    doc, _ = _load_verify()
    on = doc.get("on") or doc.get(True)
    assert on["workflow_run"]["workflows"] == ["Deploy"]
    condition = doc["jobs"]["verify"]["if"]
    assert "vars.CLAUDE_FIX_ENABLED == 'true'" in condition  # same kill switch
    assert "conclusion == 'success'" in condition  # never verify a failed deploy


def test_verifier_is_cheap_and_read_only() -> None:
    """The verifier gathers evidence — it must not be able to edit code and
    must stay on the cheapest model tier."""
    doc, text = _load_verify()
    model = re.search(r"--model (claude-[a-z0-9.-]+)", text)
    assert model is not None and "haiku" in model.group(1), "verifier must run on Haiku"
    turns = re.search(r"--max-turns (\d+)", text)
    assert turns is not None and int(turns.group(1)) <= 40
    tools = re.search(r'--allowedTools "([^"]+)"', text)
    assert tools is not None
    # no code editing AND no filesystem exploration: the first live run
    # burned its whole turn budget reading the repo instead of probing
    # prod — verification is gh + curl only.
    for forbidden in ["Edit", "Write", "Read", "Grep", "Glob", "git push", "npm", "uv run"]:
        assert forbidden not in tools.group(1), f"verifier toolset must exclude {forbidden}"
    # deterministic pre-filter: the Claude step must be conditional on the
    # free gh query so empty queues cost zero tokens
    steps = doc["jobs"]["verify"]["steps"]
    claude_step = next(s for s in steps if "claude-code-action" in str(s.get("uses", "")))
    assert "steps.pending.outputs.numbers" in claude_step["if"]


def test_verifier_carries_fence_and_label_contract() -> None:
    from dosadash_shared import LABEL_AI_VERIFIED

    _, text = _load_verify()
    assert UNTRUSTED_BEGIN in text and UNTRUSTED_END in text
    assert LABEL_AI_VERIFIED in text
    assert LABEL_AI_VERIFIED in GITHUB_LABELS
    assert "not verified" in text and "reopen" in text  # honest-failure path exists


def test_prompts_never_inline_issue_body() -> None:
    """S7 cache + injection hygiene (docs/15): `github.event.issue.body`
    must never be interpolated into an agent prompt. The agent reads the
    issue via `gh` mid-transcript instead, which (a) keeps the volatile
    untrusted text out of the cacheable prompt prefix and (b) keeps the
    fence quoting the SINGLE layer where user text enters the context."""
    for workflow in (WORKFLOW, VERIFY_WORKFLOW):
        text = workflow.read_text()
        assert "github.event.issue.body" not in text, (
            f"{workflow.name}: issue body interpolated into the workflow — "
            "read it via `gh issue view` inside the run instead"
        )


def test_hard_limit_zones_match_registry() -> None:
    """S6: HUMAN_ONLY zones are a shared registry — the fixer prompt's
    hard-limits rule must name every one, so widening the agent's blast
    radius requires editing BOTH (and this gate) deliberately."""
    _, text = _load()
    for zone in HUMAN_ONLY_ZONES:
        assert zone in text, f"fixer prompt must forbid the HUMAN_ONLY zone: {zone}"


# ------------------------------------------------- AI reviewer (Phase 15 S3)

REVIEW_WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / REVIEW_WORKFLOW_FILE
)


def _load_review() -> tuple[dict, str]:
    text = REVIEW_WORKFLOW.read_text()
    return yaml.safe_load(text), text


def test_reviewer_scoped_to_fixer_prs_with_kill_switch() -> None:
    doc, text = _load_review()
    condition = doc["jobs"]["review"]["if"]
    assert "vars.CLAUDE_REVIEW_ENABLED == 'true'" in condition
    assert f"startsWith(github.head_ref, '{FIX_BRANCH_PREFIX}')" in condition, (
        "reviewer scope must ride the shared fix-branch contract"
    )
    # superseded reviews are cancelled, not queued (cost control)
    assert doc.get("concurrency", {}).get("cancel-in-progress") is True


def test_reviewer_is_independent_cheap_and_read_only() -> None:
    """The reviewer must never be the fixer's model (no self-review), must
    stay cheap (Haiku, bounded turns), and must be read-only by
    construction — a reviewer that can edit code is just a second fixer."""
    _, review_text = _load_review()
    fix_text = WORKFLOW.read_text()
    review_model = re.search(r"--model (claude-[a-z0-9.-]+)", review_text).group(1)
    fix_model = re.search(r"--model (claude-[a-z0-9.-]+)", fix_text).group(1)
    assert review_model != fix_model, "an agent must never review its own model's work"
    assert "haiku" in review_model
    turns = int(re.search(r"--max-turns (\d+)", review_text).group(1))
    assert turns <= 20
    allowed = re.search(r'--allowedTools "([^"]+)"', review_text).group(1)
    for banned in ("Edit", "Write", "NotebookEdit"):
        assert banned not in allowed.split(","), f"reviewer toolset must exclude {banned}"
    for mutating in ("git push", "gh pr merge", "gh issue edit"):
        assert mutating not in allowed, f"reviewer must not be allowed to run {mutating}"


def test_reviewer_verdict_is_computed_and_fail_closed() -> None:
    """Dish-QC philosophy: a deterministic step parses the verdict marker;
    the agent's own exit status is never the check outcome. REQUEST_CHANGES
    fails, notes never block, and a MISSING verdict fails (fail-closed)."""
    _, text = _load_review()
    assert REVIEW_COMMENT_MARKER in text
    for verdict in REVIEW_VERDICTS:
        assert f"VERDICT: {verdict}" in text or verdict in text, (
            f"workflow must handle verdict {verdict}"
        )
    # blocking branch + fail-closed branch both exit 1
    assert text.count("exit 1") >= 2, "REQUEST_CHANGES and missing-verdict must both fail"
    assert "fail-closed" in text
    # notes never block: the passing case must include APPROVE_WITH_NOTES
    assert '"VERDICT: APPROVE"|"VERDICT: APPROVE_WITH_NOTES")' in text


def test_reviewer_carries_fence_and_checklist_contract() -> None:
    _, text = _load_review()
    assert UNTRUSTED_BEGIN in text and UNTRUSTED_END in text, (
        "issue text is untrusted for the reviewer exactly as for the fixer"
    )
    for zone in HUMAN_ONLY_ZONES:
        assert zone in text, f"reviewer checklist must cover HUMAN_ONLY zone {zone}"
    # eval-coverage rule (Hard Rule 5) must be part of the checklist
    assert "evals" in text


# ------------------------------------------- deploy canary (Phase 15 S4)

DEPLOY_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"


def test_canary_probes_public_surface_within_bounds() -> None:
    """The canary is deterministic and $0: bounded public-surface probing,
    both breach modes (sustained + flapping) explicit in the script."""
    text = DEPLOY_WORKFLOW.read_text()
    doc = yaml.safe_load(text)
    assert "canary" in doc["jobs"], "deploy must carry the post-deploy canary job"
    assert doc["jobs"]["canary"]["needs"] == "deploy"
    assert "https://" in doc["jobs"]["canary"]["env"]["BASE_URL"]
    for path in ("/healthz", "/api/v1/menu"):
        assert path in text, f"canary must probe {path}"
    # bounded window: rounds × sleep ≈ 10 min, sustained-breach early stop
    assert "seq 1 20" in text and "sleep 30" in text
    assert '"$consecutive" -ge 3' in text, "sustained-outage breach rule"
    assert "failures * 10" in text, "flapping (≥10% error rate) breach rule"


def test_canary_rollback_is_mechanical_and_merge_gated() -> None:
    """Rollback is a mechanical `git revert` PR that the FULL merge-gate
    stack decides — never a direct push to main. The PAT is required (the
    default token can't trigger CI → auto-merge would starve, Phase-13
    lesson) and a revert war is structurally impossible."""
    text = DEPLOY_WORKFLOW.read_text()
    assert "git revert --no-edit" in text
    assert "gh pr merge --auto --squash" in text
    assert "git push origin" in text and "git push origin main" not in text
    assert "CLAUDE_FIX_GH_TOKEN" in text, "default token would starve auto-merge of CI"
    # oscillation guard: an auto-rollback deploy never auto-reverts again
    assert '"revert: auto-rollback"' in text
    assert "refus" in text  # refusing a revert war is stated, not implied
    # breach fails the Deploy run → the verifier (success-only) stays away
    assert "exit 1" in text


def test_canary_incident_report_rides_the_sentinel_spine() -> None:
    from typing import get_args

    from dosadash_shared import SentinelIncidentIn

    text = DEPLOY_WORKFLOW.read_text()
    assert "secrets.CANARY_REPORT_URL" in text  # never a hardcoded URL
    assert "X-Internal-Token" in text
    assert "non-blocking" in text  # filing failure must never mask the rollback
    kinds = get_args(SentinelIncidentIn.model_fields["kind"].annotation)
    assert '"kind":"deploy_canary_failed"' in text
    assert "deploy_canary_failed" in kinds, "workflow kind must stay in the shared allowlist"

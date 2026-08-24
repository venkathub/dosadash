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
    FIXER_TRIGGER_LABELS,
    GITHUB_LABELS,
    LABEL_AI_AUTO_FIX,
    LABEL_AI_NEEDS_APPROVAL,
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
    for forbidden in ["Edit", "Write", "git push", "npm", "uv run"]:
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

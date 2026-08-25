"""Self-healing feedback loop schemas (Phase 13, docs/14).

Trust model:
- Report text is END-USER INPUT. The api redacts phones (Hard Rule 8)
  before the row is stored or mirrored to GitHub, and the issue body wraps
  it in explicit UNTRUSTED markers so downstream agents (triage LLM, the
  Claude fixer) treat it as data, never as instructions.
- GitHub labels are the authoritative automation signal (the fixer
  workflow triggers on them); `FeedbackStatus` is the local projection so
  the admin tab never needs a GitHub round-trip.
- Nothing in this pipeline may sit on the customer's critical path: a
  GitHub outage degrades to store-locally (hotfix-#72 pattern).
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FEEDBACK_TRIAGE_PROMPT_VERSION = "feedback_triage_v1"

# ------------------------------------------------------------------- labels
# The GitHub label registry — single source of truth for the api (creates
# and applies them), the triage policy (emits them), the Telegram approval
# flow (flips them), and the fixer workflow's trigger filter. Coherence
# between this dict and .github/workflows/claude-issue-fix.yml is eval-gated.

LABEL_USER_REPORTED = "user-reported"
LABEL_BUG = "bug"
LABEL_FEATURE = "feature"
LABEL_AI_AUTO_FIX = "ai:auto-fix"
LABEL_AI_NEEDS_APPROVAL = "ai:needs-approval"
LABEL_AI_APPROVED = "ai:approved"
LABEL_AI_REJECTED = "ai:rejected"
LABEL_AI_FIXED = "ai:fixed"
LABEL_AI_VERIFIED = "ai:verified"

# name -> (color hex without '#', description)
GITHUB_LABELS: dict[str, tuple[str, str]] = {
    LABEL_USER_REPORTED: ("1B1B3A", "Raised from the DosaDash GUI feedback button"),
    LABEL_BUG: ("D6336C", "User-reported defect"),
    LABEL_FEATURE: ("F2B705", "User-requested feature"),
    LABEL_AI_AUTO_FIX: ("5BD69B", "Triage verdict: small low-risk bug — fixer may auto-merge"),
    LABEL_AI_NEEDS_APPROVAL: ("FF8B8B", "Triage verdict: human approval required to run fixer"),
    LABEL_AI_APPROVED: ("2DA44E", "Admin approved via Telegram/backoffice — fixer may run"),
    LABEL_AI_REJECTED: ("6E7781", "Admin rejected — fixer must not run"),
    LABEL_AI_FIXED: ("8250DF", "Fixer PR merged"),
    LABEL_AI_VERIFIED: ("0E8A16", "Fix verified live in production by the verifier agent"),
}

# The fixer workflow may only trigger on these two labels. Kept here so the
# asset gate can assert the workflow file and the registry never drift.
FIXER_TRIGGER_LABELS: tuple[str, str] = (LABEL_AI_AUTO_FIX, LABEL_AI_APPROVED)

# ---------------------------------------------------------- untrusted fence
# Issue-body markers around raw user text. The fixer prompt instructs the
# agent that fenced content is data-only; the fence strings are constants so
# api (writer), workflow prompt (reader), and eval gates agree byte-for-byte.

UNTRUSTED_BEGIN = "<!-- UNTRUSTED USER CONTENT BEGIN -->"
UNTRUSTED_END = "<!-- UNTRUSTED USER CONTENT END -->"


class FeedbackType(StrEnum):
    BUG = "BUG"
    FEATURE = "FEATURE"


class ReporterTier(StrEnum):
    """Who raised it — drives triage trust (STAFF feature requests are
    auto-implementation candidates; ANON reports never are)."""

    ANON = "ANON"
    CUSTOMER = "CUSTOMER"
    STAFF = "STAFF"


class FeedbackStatus(StrEnum):
    RECEIVED = "RECEIVED"  # stored locally; GitHub mirror pending/failed
    TRACKED = "TRACKED"  # GitHub issue open, awaiting triage
    AUTO_FIX = "AUTO_FIX"  # triage: small low-risk bug — fixer dispatched
    NEEDS_APPROVAL = "NEEDS_APPROVAL"  # triage: waiting on admin decision
    APPROVED = "APPROVED"  # admin said yes — fixer dispatched
    REJECTED = "REJECTED"  # admin said no
    FIXING = "FIXING"  # Phase 14: fixer trigger label landed — agent running
    PR_OPEN = "PR_OPEN"  # Phase 14: fix PR opened, merge gates running
    FIXED = "FIXED"  # fixer PR merged
    VERIFIED = "VERIFIED"  # Phase 14: verifier confirmed the fix live in prod
    REOPENED = "REOPENED"  # Phase 14: verifier (or a human) reopened the issue
    DISMISSED = "DISMISSED"  # closed without action (spam/not actionable)


class FeedbackContext(BaseModel):
    """Client-supplied situational context (all optional, all length-capped —
    this is still untrusted input, it just isn't free prose)."""

    route: str | None = Field(default=None, max_length=200)
    commit_sha: str | None = Field(default=None, max_length=40)
    user_agent: str | None = Field(default=None, max_length=300)


class FeedbackCreateIn(BaseModel):
    type: FeedbackType
    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=10, max_length=2000)
    context: FeedbackContext | None = None


class FeedbackOut(BaseModel):
    """Customer wire shape — no triage internals, no reporter details."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    type: FeedbackType
    status: FeedbackStatus
    title: str
    github_issue_number: int | None = None
    created_at: datetime
    duplicate: bool = False  # True → an open twin already exists; no new issue


# ---------------------------------------------------- lifecycle (Phase 14)
# The loop's tail (fixer run → PR → merge → verify) happens on GitHub;
# Phase 14 syncs it back so the portal, Telegram, and metrics never need a
# GitHub round-trip. `FeedbackEventStage` is the append-only timeline
# vocabulary; `LABEL_STATUS_PRECEDENCE` is how the reconciler derives the
# local status projection from an issue's current label set (highest first
# — an issue carrying both ai:fixed and ai:verified is VERIFIED).


class FeedbackEventStage(StrEnum):
    RECEIVED = "RECEIVED"  # report stored locally
    TRACKED = "TRACKED"  # GitHub issue created (intake or re-mirror)
    TRIAGED = "TRIAGED"  # triage verdict recorded (verdict in payload)
    APPROVED = "APPROVED"  # human approved (Telegram or admin tab)
    REJECTED = "REJECTED"  # human rejected
    FIX_STARTED = "FIX_STARTED"  # fixer trigger label landed on the issue
    RCA_POSTED = "RCA_POSTED"  # "## Root cause analysis" comment
    ESCALATED = "ESCALATED"  # fixer hit a hard limit → back to approval
    PR_OPENED = "PR_OPENED"  # fix PR opened
    PR_CLOSED = "PR_CLOSED"  # fix PR closed WITHOUT merging
    PR_MERGED = "PR_MERGED"  # fix PR merged
    FIXED = "FIXED"  # ai:fixed label (fixer's own completion signal)
    VERIFICATION_POSTED = "VERIFICATION_POSTED"  # "## Prod verification" comment
    VERIFIED = "VERIFIED"  # ai:verified label
    REOPENED = "REOPENED"  # issue reopened (verifier or human)
    CLOSED = "CLOSED"  # issue closed on GitHub
    DISMISSED = "DISMISSED"  # triage: not actionable
    SYNCED = "SYNCED"  # reconciler corrected local status from labels


# Reconciler mapping: issue labels → local status, highest precedence first.
# Coherence with GITHUB_LABELS is eval-gated (every ai:* label that implies
# a status must appear exactly once here).
LABEL_STATUS_PRECEDENCE: tuple[tuple[str, FeedbackStatus], ...] = (
    (LABEL_AI_VERIFIED, FeedbackStatus.VERIFIED),
    (LABEL_AI_FIXED, FeedbackStatus.FIXED),
    (LABEL_AI_REJECTED, FeedbackStatus.REJECTED),
    (LABEL_AI_APPROVED, FeedbackStatus.APPROVED),
    (LABEL_AI_NEEDS_APPROVAL, FeedbackStatus.NEEDS_APPROVAL),
    (LABEL_AI_AUTO_FIX, FeedbackStatus.AUTO_FIX),
)

# Comment markers the workflows write (byte-agreement is eval-gated against
# the workflow files, same discipline as the untrusted fence).
RCA_COMMENT_MARKER = "## Root cause analysis"
VERIFICATION_COMMENT_MARKER = "## Prod verification"

# The fixer's branch naming contract — how PR webhook events map back to
# their issue without a GitHub round-trip.
FIX_BRANCH_PREFIX = "fix/issue-"


class FeedbackEventOut(BaseModel):
    """One timeline entry (portal drill-down + Telegram lifecycle feed)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    stage: FeedbackEventStage
    actor: str | None = None  # "webhook:github" | "reconciler" | "admin:<id>" | "system"
    payload: dict | None = None
    created_at: datetime


class FeedbackEventListOut(BaseModel):
    events: list[FeedbackEventOut]


class AdminFeedbackOut(BaseModel):
    """Backoffice wire shape — full row incl. triage provenance."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None = None
    reporter_tier: ReporterTier
    type: FeedbackType
    status: FeedbackStatus
    title: str
    description: str
    context: dict | None = None
    dedupe_hash: str
    github_issue_number: int | None = None
    github_error: str | None = None
    triage: dict | None = None
    fix_pr_number: int | None = None  # Phase 14: the fixer's PR, once opened
    verified_at: datetime | None = None  # Phase 14: verifier sign-off time
    created_at: datetime
    updated_at: datetime


class AdminFeedbackListOut(BaseModel):
    """List wrapper: `github_repo` lets the admin tab deep-link issues
    without shipping the token-bearing client config to the browser."""

    reports: list[AdminFeedbackOut]
    total: int
    github_repo: str  # "" when the integration is disabled


# ------------------------------------------------------------------- triage
# Inventory-agent pattern: the LLM only OBSERVES (TriageAssessment); the
# verdict is COMPUTED by a deterministic policy (dish-QC philosophy). A
# report can therefore never talk its way into ai:auto-fix — the policy
# requires user-filed BUG ∧ model-read BUG ∧ effort S ∧ risk LOW.


class TriageVerdict(StrEnum):
    AUTO_FIX = "AUTO_FIX"  # small low-risk bug — fixer may auto-merge
    NEEDS_APPROVAL = "NEEDS_APPROVAL"  # human decides before the fixer runs
    DISMISS = "DISMISS"  # not actionable (rant/spam/unclear)


class TriageAssessment(BaseModel):
    """Structured LLM output for one report (Hard Rule 3). Observations
    only — the deterministic policy owns the verdict."""

    actionable: bool
    type: FeedbackType  # the model's read (a feature disguised as a bug)
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    effort: Literal["S", "M", "L"]
    risk: Literal["LOW", "HIGH"]
    area: str = Field(default="", max_length=120)  # guessed code area
    summary: str = Field(min_length=1, max_length=300)


class FeedbackTriageRequest(BaseModel):
    """api/worker → ai: one report (text already redacted api-side; the ai
    redacts again defensively before the LLM — Rule 8 twice over)."""

    report_id: int
    type: FeedbackType
    title: str = Field(max_length=120)
    description: str = Field(max_length=2000)
    reporter_tier: ReporterTier


class FeedbackTriageResponse(BaseModel):
    report_id: int
    verdict: TriageVerdict
    assessment: TriageAssessment | None = None  # None → LLM chain fell back
    labels: list[str] = []  # GitHub labels the caller should apply
    fallback: bool = False
    violations: list[str] = []
    model: str | None = None
    prompt_version: str = FEEDBACK_TRIAGE_PROMPT_VERSION

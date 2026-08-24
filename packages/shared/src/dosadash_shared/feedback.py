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
    FIXED = "FIXED"  # fixer PR merged
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
    created_at: datetime
    updated_at: datetime


class AdminFeedbackListOut(BaseModel):
    """List wrapper: `github_repo` lets the admin tab deep-link issues
    without shipping the token-bearing client config to the browser."""

    reports: list[AdminFeedbackOut]
    total: int
    github_repo: str  # "" when the integration is disabled

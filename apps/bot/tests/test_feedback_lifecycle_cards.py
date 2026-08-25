"""Lifecycle anchor cards + ping texts (Phase 14 slice 2) — pure render."""

from datetime import UTC, datetime, timedelta

from dosadash_bot import render


def _payload(**overrides) -> dict:
    base = {
        "report_id": 12,
        "type": "BUG",
        "title": "Cart total wrong",
        "status": "FIXING",
        "stage": "FIX_STARTED",
        "github_url": "https://github.com/owner/repo/issues/120",
        "timeline": [
            {"stage": "RECEIVED", "at": "2026-08-20T04:32:00", "note": None},
            {"stage": "TRACKED", "at": "2026-08-20T04:33:00", "note": "issue #120"},
            {"stage": "TRIAGED", "at": "2026-08-20T04:45:00", "note": "AUTO_FIX"},
            {"stage": "FIX_STARTED", "at": "2026-08-20T04:46:00", "note": None},
        ],
        "ping": False,
    }
    base.update(overrides)
    return base


def test_anchor_card_full() -> None:
    text = render.feedback_lifecycle_text(_payload())
    assert "🐞 Bug report #12" in text
    assert "“Cart total wrong”" in text
    assert "Status: 🔧 AI fixer working" in text
    assert "📥 Received" in text
    assert "📌 Filed on GitHub (issue #120)" in text
    assert "🔎 Triaged (AUTO_FIX)" in text
    assert "🤖 AI fixer dispatched" in text
    assert "🔗 https://github.com/owner/repo/issues/120" in text


def test_feature_card_without_issue() -> None:
    text = render.feedback_lifecycle_text(
        _payload(type="FEATURE", github_url=None, status="NEEDS_APPROVAL")
    )
    assert text.startswith("✨ Feature report #12")
    assert "🟡 Awaiting your decision" in text
    assert "🔗" not in text


def test_unknown_stage_and_status_render_raw() -> None:
    text = render.feedback_lifecycle_text(
        _payload(status="SOMETHING_NEW", timeline=[{"stage": "NEW_STAGE", "at": None}])
    )
    assert "Status: SOMETHING_NEW" in text
    assert "— · NEW_STAGE" in text  # missing timestamp renders as em-dash


def test_timeline_time_today_is_compact() -> None:
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    stamp = render._timeline_time(now_utc.isoformat())
    assert len(stamp) == 5 and ":" in stamp  # HH:MM


def test_timeline_time_old_date_carries_day() -> None:
    old = (datetime.now(UTC) - timedelta(days=40)).replace(tzinfo=None)
    stamp = render._timeline_time(old.isoformat())
    assert any(c.isalpha() for c in stamp)  # "20 Jul 10:02"


def test_timeline_time_garbage_is_safe() -> None:
    assert render._timeline_time("not-a-date") == "—"
    assert render._timeline_time(None) == "—"


def test_ping_texts() -> None:
    assert "hard limit" in render.feedback_ping_text("ESCALATED", 12)
    assert "verified live" in render.feedback_ping_text("VERIFIED", 12)
    assert "reopened" in render.feedback_ping_text("REOPENED", 12)
    fallback = render.feedback_ping_text("PR_MERGED", 12)
    assert "#12" in fallback and "Fix PR merged" in fallback

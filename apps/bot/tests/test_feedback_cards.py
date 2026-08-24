"""Feedback approval cards (Phase 13): keyboards + render texts as data —
repo precedent: aiogram types are constructed and inspected, never sent."""

from dosadash_bot import render
from dosadash_bot.main import feedback_keyboard


def test_feedback_keyboard_callback_data() -> None:
    kb = feedback_keyboard(42)
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert [b.text for b in buttons] == ["✅ Approve", "🚫 Reject"]
    assert [b.callback_data for b in buttons] == ["fb:approve:42", "fb:reject:42"]


def test_feedback_notify_text_bug_card() -> None:
    text = render.feedback_notify_text(
        {
            "report_id": 7,
            "type": "BUG",
            "title": "Cart total wrong",
            "summary": "GST line can go negative with stacked coupons",
            "effort": "M",
            "risk": "HIGH",
            "github_url": "https://github.com/o/r/issues/12",
        }
    )
    assert "🐞 Bug report #7" in text
    assert "“Cart total wrong”" in text
    assert "🤖 GST line can go negative" in text
    assert "(effort M, risk HIGH)" in text
    assert "https://github.com/o/r/issues/12" in text
    assert "Approve to let the AI fixer" in text


def test_feedback_notify_text_minimal_feature() -> None:
    text = render.feedback_notify_text({"report_id": 9, "type": "FEATURE", "title": "Dark mode"})
    assert "✨ Feature report #9" in text
    assert "🤖" not in text  # no triage summary → no fake provenance line


def test_feedback_decided_texts() -> None:
    assert "✅ Report #3 approved" in render.feedback_decided_text(3, "APPROVED", None)
    assert render.feedback_decided_text(3, "REJECTED", None) == "🚫 Report #3 rejected."
    fallback = render.feedback_decided_text(3, None, None)
    assert fallback.startswith("⚠️") and "backoffice" in fallback
    assert "state error" in render.feedback_decided_text(3, None, "state error")

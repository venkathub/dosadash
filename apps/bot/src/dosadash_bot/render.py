"""Message rendering helpers (pure, unit-tested).

Hard Rule 10: the bot only normalizes I/O. These helpers render apps/ai
agent responses (Phase 3) and the account-linking flow (Phase 1).
"""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo


def welcome_text(first_name: str | None) -> str:
    name = first_name or "there"
    return (
        f"🥞 Vanakkam {name}! Welcome to DosaDash.\n\n"
        "I'm the DosaDash ordering assistant — tell me what you'd like "
        '("2 masala dosas and a filter coffee"), send a voice note 🎤 '
        "(English or Tamil), or ask about the menu, allergens, or delivery. "
        "When your order looks right, tap ✅ Place order."
    )


def unsupported_text() -> str:
    return "🥞 I understand text and voice notes 🎤 — photos and stickers are beyond me!"


def typing_text() -> str:
    return "🥞 …"


def stream_progress_text(partial: str) -> str:
    return f"🥞 {partial} ▌"


def final_text(final: dict[str, Any]) -> str:
    """Reply + validated draft summary + warnings, as one Telegram message."""
    lines = [f"🥞 {final.get('reply', '')}".strip()]
    draft = final.get("draft") or {}
    items = draft.get("items") or []
    if items:
        lines.append("")
        lines.append("🧾 Your order so far:")
        for item in items:
            price = float(item["unit_price"]) * item["qty"]
            note = f" ({item['notes']})" if item.get("notes") else ""
            lines.append(f"  • {item['qty']}× {item['name']}{note} — ₹{price:.0f}")
        lines.append(f"  Subtotal: ₹{float(draft['subtotal']):.2f} + GST")
    for warning in final.get("warnings") or []:
        lines.append(f"⚠️ {warning}")
    return "\n".join(lines)


def error_text() -> str:
    return "⚠️ Sorry, the assistant is unavailable right now — please try again in a moment."


def voice_heard_text(transcript: str) -> str:
    """Echo the (PII-redacted) transcript so the customer can catch
    mishearings before the agent acts on them."""
    return f"🎤 I heard: “{transcript}”"


def voice_failed_text() -> str:
    return (
        "🎤 Sorry, I couldn't make out that voice note — please try again "
        "or type your order instead."
    )


def voice_too_long_text(limit_seconds: int) -> str:
    return f"🎤 That voice note is a bit long — please keep it under {limit_seconds} seconds."


def order_placed_text(order_id: int, total: str, public_web_url: str) -> str:
    return (
        f"✅ Order #{order_id} placed! Total ₹{total} (incl. GST).\n\n"
        f"Track and pay here: {public_web_url}/?track={order_id}\n"
        "(Demo deployment — Razorpay TEST mode, no real charges.)"
    )


def place_failed_text(detail: str | None) -> str:
    if detail == "Telegram account not linked":
        return (
            "🔗 Almost! Link your DosaDash account first so I know who's "
            "ordering: on the website go to Orders → Link Telegram, then try again."
        )
    return f"⚠️ Couldn't place the order: {detail or 'please try again.'}"


def draft_cleared_text() -> str:
    return "🧹 Draft cleared. What would you like instead?"


def link_success_text(name: str | None) -> str:
    who = name or "friend"
    return (
        f"🔗 Done, {who}! Your DosaDash account is now linked.\n\n"
        "Login OTPs will arrive here instead of the demo banner, and "
        "order updates will follow soon."
    )


def po_notify_text(payload: dict[str, Any]) -> str:
    """Owner approval card for an agent-drafted purchase order (Phase 6)."""
    supplier = payload.get("supplier_name") or "Unassigned supplier"
    lines = [f"📦 Purchase order #{payload['po_id']} — {supplier}", ""]
    for line in payload.get("lines") or []:
        lines.append(f"  • {line['name']}: {line['qty']} {line['unit']}")
    cost = payload.get("expected_cost")
    if cost:
        lines.append(f"  Expected cost: ₹{float(cost):.0f}")
    rationale = payload.get("rationale")
    if rationale:
        lines.append("")
        lines.append(f"🤖 {rationale}")
    lines.append("")
    lines.append("Approve to send it to the supplier, or reject it.")
    return "\n".join(lines)


def po_decided_text(po_id: int, status: str | None, detail: str | None) -> str:
    if status == "APPROVED":
        return f"✅ PO #{po_id} approved. Mark it received in the backoffice when goods arrive."
    if status == "REJECTED":
        return f"🚫 PO #{po_id} rejected."
    return f"⚠️ Couldn't update PO #{po_id}: {detail or 'please use the backoffice.'}"


def link_failed_text(detail: str | None) -> str:
    reason = detail or "The link code is invalid or expired."
    return (
        f"⚠️ Couldn't link your account: {reason}\n\n"
        "Generate a fresh link from the DosaDash website (Orders → Link Telegram) "
        "and tap it within 10 minutes."
    )


def feedback_notify_text(payload: dict[str, Any]) -> str:
    """Admin approval card for a triaged feedback report (Phase 13).

    The title is END-USER text — rendered verbatim inside the card but
    never interpreted; approval only flips a GitHub label api-side."""
    kind = "🐞 Bug" if payload.get("type") == "BUG" else "✨ Feature"
    lines = [f"{kind} report #{payload['report_id']} needs a decision", ""]
    lines.append(f"“{payload.get('title', '')}”")
    summary = payload.get("summary")
    if summary:
        effort = payload.get("effort") or "?"
        risk = payload.get("risk") or "?"
        lines.append("")
        lines.append(f"🤖 {summary} (effort {effort}, risk {risk})")
    issue = payload.get("github_url")
    if issue:
        lines.append(f"🔗 {issue}")
    lines.append("")
    lines.append("Approve to let the AI fixer implement it, or reject it.")
    return "\n".join(lines)


def feedback_decided_text(report_id: int, status: str | None, detail: str | None) -> str:
    if status == "APPROVED":
        return (
            f"✅ Report #{report_id} approved — the AI fixer will pick it up "
            "and open a PR for review."
        )
    if status == "REJECTED":
        return f"🚫 Report #{report_id} rejected."
    return f"⚠️ Couldn't update report #{report_id}: {detail or 'please use the backoffice.'}"


# ------------------------------------------------- lifecycle feed (Phase 14)
# One anchor status card per report per linked admin, edited in place on
# every stage (silent — Telegram edits don't notify); ping replies fire
# only for actionable/terminal stages. Payload comes pre-extracted from
# the api (data); everything visual lives here (Hard Rule 10).

_STAGE_LINES: dict[str, str] = {
    "RECEIVED": "📥 Received",
    "TRACKED": "📌 Filed on GitHub",
    "TRIAGED": "🔎 Triaged",
    "APPROVED": "✅ Approved",
    "REJECTED": "🚫 Rejected",
    "FIX_STARTED": "🤖 AI fixer dispatched",
    "RCA_POSTED": "🧠 Root cause posted",
    "ESCALATED": "🛑 Fixer escalated — needs approval",
    "PR_OPENED": "🔀 Fix PR opened",
    "PR_CLOSED": "❌ Fix PR closed unmerged",
    "PR_MERGED": "🎉 Fix PR merged",
    "FIXED": "🧩 Fix landed",
    "VERIFICATION_POSTED": "🧪 Prod verification posted",
    "VERIFIED": "🏁 Verified live in prod",
    "REOPENED": "⚠️ Reopened — fix didn't hold",
    "CLOSED": "🗂 Issue closed",
    "DISMISSED": "🚮 Dismissed by triage",
    "SYNCED": "🔁 Synced from GitHub",
}

_STATUS_HEADLINES: dict[str, str] = {
    "RECEIVED": "📥 Received",
    "TRACKED": "📌 Tracked — awaiting triage",
    "AUTO_FIX": "🤖 Queued for auto-fix",
    "NEEDS_APPROVAL": "🟡 Awaiting your decision",
    "APPROVED": "✅ Approved — fixer queued",
    "REJECTED": "🚫 Rejected",
    "FIXING": "🔧 AI fixer working",
    "PR_OPEN": "🔀 Fix PR running the merge gates",
    "FIXED": "🎉 Fix merged — deploying",
    "VERIFIED": "🏁 Verified in production",
    "REOPENED": "⚠️ Reopened",
    "DISMISSED": "🚮 Dismissed",
}


def _timeline_time(iso: str | None) -> str:
    """Compact IST timestamp: HH:MM today, `d Mon HH:MM` otherwise.
    Event times are naive UTC (DB convention)."""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    ist = dt.astimezone(ZoneInfo("Asia/Kolkata"))
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    if ist.date() == now.date():
        return ist.strftime("%H:%M")
    return ist.strftime("%d %b %H:%M")


def feedback_lifecycle_text(payload: dict[str, Any]) -> str:
    """The anchor status card. Title is END-USER text — rendered verbatim,
    never interpreted."""
    kind = "🐞 Bug" if payload.get("type") == "BUG" else "✨ Feature"
    status = payload.get("status") or ""
    headline = _STATUS_HEADLINES.get(status, status)
    lines = [
        f"{kind} report #{payload['report_id']} — “{payload.get('title', '')}”",
        f"Status: {headline}",
        "",
    ]
    for entry in payload.get("timeline") or []:
        stage_line = _STAGE_LINES.get(entry.get("stage") or "", entry.get("stage") or "?")
        note = entry.get("note")
        suffix = f" ({note})" if note else ""
        lines.append(f"{_timeline_time(entry.get('at'))} · {stage_line}{suffix}")
    issue = payload.get("github_url")
    if issue:
        lines.append("")
        lines.append(f"🔗 {issue}")
    return "\n".join(lines)


def feedback_ping_text(stage: str, report_id: int) -> str:
    """Audible reply under the anchor — only for stages worth a sound."""
    if stage == "ESCALATED":
        return (
            f"🛑 Report #{report_id}: the AI fixer hit a hard limit and "
            "handed back — your decision is needed."
        )
    if stage == "VERIFIED":
        return f"🏁 Report #{report_id}: fix verified live in production."
    if stage == "REOPENED":
        return f"⚠️ Report #{report_id}: verification failed — the issue was reopened."
    return f"🔔 Report #{report_id}: {_STAGE_LINES.get(stage, stage)}"

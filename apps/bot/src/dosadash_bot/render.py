"""Message rendering helpers (pure, unit-tested).

Hard Rule 10: the bot only normalizes I/O. These helpers render apps/ai
agent responses (Phase 3) and the account-linking flow (Phase 1).
"""

from typing import Any


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

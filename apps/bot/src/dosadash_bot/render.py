"""Message rendering helpers (pure, unit-tested).

Hard Rule 10: the bot only normalizes I/O. From Phase 3 these helpers render
apps/ai agent responses; for Phase 0 they power the webhook echo.
"""


def welcome_text(first_name: str | None) -> str:
    name = first_name or "there"
    return (
        f"🥞 Vanakkam {name}! Welcome to DosaDash.\n\n"
        "I'm the DosaDash ordering assistant. Conversational ordering arrives "
        "soon — for now I'll echo whatever you send me (Phase 0 wiring check)."
    )


def echo_text(text: str | None) -> str:
    if not text:
        return "🥞 I can only handle text messages for now."
    return f"🥞 Echo: {text}"


def link_success_text(name: str | None) -> str:
    who = name or "friend"
    return (
        f"🔗 Done, {who}! Your DosaDash account is now linked.\n\n"
        "Login OTPs will arrive here instead of the demo banner, and "
        "order updates will follow soon."
    )


def link_failed_text(detail: str | None) -> str:
    reason = detail or "The link code is invalid or expired."
    return (
        f"⚠️ Couldn't link your account: {reason}\n\n"
        "Generate a fresh link from the DosaDash website (Orders → Link Telegram) "
        "and tap it within 10 minutes."
    )

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

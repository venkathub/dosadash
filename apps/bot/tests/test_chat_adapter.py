"""Chat adapter units: SSE parsing, state transitions, rendering, throttle."""

from dosadash_bot import render, state
from dosadash_bot.api_client import parse_sse
from dosadash_bot.main import EditThrottle, draft_keyboard

FINAL = {
    "reply": "Two Masala Dosas and one Filter Coffee — anything else?",
    "draft": {
        "items": [
            {"item_id": 1, "name": "Masala Dosa", "qty": 2, "unit_price": "120.00", "notes": None},
            {
                "item_id": 3,
                "name": "Filter Coffee",
                "qty": 1,
                "unit_price": "60.00",
                "notes": "less sugar",
            },
        ],
        "subtotal": "300.00",
    },
    "ready_to_place": False,
    "warnings": ["Heads up: Filter Coffee contains milk (in your allergen list)."],
    "kitchen_open": True,
}

# -------------------------------------------------------------------- parse_sse


def test_parse_sse_across_chunks():
    events, buffer = parse_sse("", 'data: {"type": "delta", "text": "Two"}\n\ndata: {"ty')
    assert events == [{"type": "delta", "text": "Two"}]
    events, buffer = parse_sse(buffer, 'pe": "final", "data": {}}\n\n')
    assert events == [{"type": "final", "data": {}}]
    assert buffer == ""


def test_parse_sse_skips_malformed_frames():
    events, _ = parse_sse("", 'data: not-json\n\ndata: {"type": "delta", "text": "x"}\n\n')
    assert events == [{"type": "delta", "text": "x"}]


# ------------------------------------------------------------------------ state


def test_record_turn_keeps_history_and_validated_draft():
    s = state.ChatState()
    state.record_turn(s, "2 masala dosas and a coffee", FINAL)
    assert s.history[-1]["role"] == "assistant"
    assert s.draft is not None and len(s.draft["items"]) == 2
    assert state.draft_order_items(s) == [
        {"item_id": 1, "qty": 2},
        {"item_id": 3, "qty": 1},
    ]


def test_empty_draft_clears_state_draft():
    s = state.ChatState(draft=FINAL["draft"])
    state.record_turn(s, "clear it", {"reply": "Cleared!", "draft": {"items": [], "subtotal": "0"}})
    assert s.draft is None
    assert state.draft_order_items(s) == []


def test_history_is_bounded():
    s = state.ChatState()
    for i in range(30):
        state.record_turn(s, f"msg {i}", {"reply": "ok", "draft": None})
    assert len(s.history) == 24


# ----------------------------------------------------------------------- render


def test_final_text_renders_draft_and_warnings():
    text = render.final_text(FINAL)
    assert "2× Masala Dosa — ₹240" in text
    assert "1× Filter Coffee (less sugar) — ₹60" in text
    assert "Subtotal: ₹300.00 + GST" in text
    assert "⚠️ Heads up: Filter Coffee contains milk" in text


def test_final_text_without_draft_is_just_reply():
    text = render.final_text({"reply": "We open at 8am.", "draft": None, "warnings": []})
    assert text == "🥞 We open at 8am."


def test_order_placed_and_link_prompt():
    placed = render.order_placed_text(42, "315.00", "https://dosadash.example")
    assert "#42" in placed and "?track=42" in placed and "TEST mode" in placed
    assert "Link your DosaDash account" in render.place_failed_text("Telegram account not linked")


# -------------------------------------------------------------------- throttle


def test_edit_throttle_limits_rate():
    throttle = EditThrottle(interval=1000)  # effectively once
    assert throttle.ready() is True
    assert throttle.ready() is False


def test_draft_keyboard_only_with_draft():
    assert draft_keyboard(False) is None
    keyboard = draft_keyboard(True)
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert labels == ["✅ Place order", "🧹 Clear"]


# ------------------------------------------------------------- voice (Phase 7)


def test_normalize_voice_mime():
    from dosadash_bot.main import normalize_voice_mime

    assert normalize_voice_mime("audio/ogg") == "audio/ogg"
    assert normalize_voice_mime("audio/mpeg") == "audio/mpeg"
    # Telegram quirks / unknown containers fall back to the voice-note default
    assert normalize_voice_mime("audio/ogg; codecs=opus") == "audio/ogg"
    assert normalize_voice_mime(None) == "audio/ogg"


def test_voice_render_texts():
    heard = render.voice_heard_text("two masala dosas and one filter coffee")
    assert "🎤" in heard and "two masala dosas" in heard
    assert "type your order" in render.voice_failed_text()
    assert "90 seconds" in render.voice_too_long_text(90)
    # voice is live now — the fallback copy must not promise it "soon"
    assert "soon" not in render.unsupported_text()
    assert "voice note" in render.welcome_text("Meera")

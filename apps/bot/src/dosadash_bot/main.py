"""aiogram webhook entrypoint — Telegram adapter for the apps/ai order agent.

Webhook mode (not polling) per CLAUDE.md; Telegram posts updates to
PUBLIC_BASE_URL + /tg/webhook, protected by a secret token header.

Hard Rule 10: no reasoning here. The bot streams the agent's reply into a
progressively edited message (draft-edit streaming), renders the validated
draft, and forwards button taps to the api.
"""

import base64
import logging
import secrets
import time
from collections.abc import Awaitable, Callable

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from dosadash_bot import render, state
from dosadash_bot.api_client import (
    link_account,
    place_order,
    po_decision,
    stream_chat,
    transcribe_voice,
)
from dosadash_bot.config import Settings, get_settings

logger = logging.getLogger("dosadash_bot")

router = Router()

_EDIT_INTERVAL_SECONDS = 0.9  # Telegram edit rate — stay well under limits


class EditThrottle:
    """At most one message edit per interval (final edit is never throttled)."""

    def __init__(self, interval: float = _EDIT_INTERVAL_SECONDS) -> None:
        self._interval = interval
        self._last: float | None = None  # None → first call always passes

    def ready(self) -> bool:
        now = time.monotonic()
        if self._last is None or now - self._last >= self._interval:
            self._last = now
            return True
        return False


def po_keyboard(po_id: int) -> InlineKeyboardMarkup:
    """Owner approval buttons (Phase 6). RBAC lives in the api, not here."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"po:approve:{po_id}"),
                InlineKeyboardButton(text="🚫 Reject", callback_data=f"po:reject:{po_id}"),
            ]
        ]
    )


def draft_keyboard(has_draft: bool) -> InlineKeyboardMarkup | None:
    if not has_draft:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Place order", callback_data="chat:place"),
                InlineKeyboardButton(text="🧹 Clear", callback_data="chat:clear"),
            ]
        ]
    )


async def _safe_edit(message: Message, text: str, **kwargs: object) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest:  # "message is not modified" and friends
        pass


@router.message(CommandStart(deep_link=True))
async def on_start_deep_link(message: Message, command: CommandObject) -> None:
    """/start <code> — account-linking deep link from the website."""
    user = message.from_user
    code = (command.args or "").strip()
    if not code or user is None:
        await message.answer(render.welcome_text(user.first_name if user else None))
        return
    settings = get_settings()
    result = await link_account(
        api_base_url=settings.api_base_url,
        internal_token=settings.internal_api_token,
        code=code,
        tg_user_id=user.id,
        tg_name=user.first_name,
    )
    if result.ok:
        await message.answer(render.link_success_text(result.name or user.first_name))
    else:
        await message.answer(render.link_failed_text(result.detail))


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    user = message.from_user
    state.reset(message.chat.id)
    await message.answer(render.welcome_text(user.first_name if user else None))


@router.message(F.text)
async def on_message(message: Message) -> None:
    await run_agent_turn(message, message.text or "")


async def run_agent_turn(message: Message, text: str) -> None:
    """One agent turn with draft-edit streaming: send a placeholder, edit it
    with reply text as tokens arrive, finish with draft + buttons. Shared by
    typed text and voice transcripts — voice is an input mode, not a new
    agent (same graph, same guardrails)."""
    settings = get_settings()
    chat_state = state.get_state(message.chat.id)
    sent = await message.answer(render.typing_text())
    throttle = EditThrottle()
    partial = ""
    final = None

    async for event in stream_chat(
        api_base_url=settings.api_base_url,
        internal_token=settings.internal_api_token,
        tg_user_id=message.from_user.id if message.from_user else message.chat.id,
        message=text,
        history=chat_state.history,
        draft=chat_state.draft,
    ):
        if event["type"] == "delta":
            partial += event["text"]
            if throttle.ready():
                await _safe_edit(sent, render.stream_progress_text(partial))
        elif event["type"] == "final":
            final = event["data"]
        elif event["type"] == "error":
            logger.warning("agent stream error: %s", event.get("detail"))

    if final is None:
        await _safe_edit(sent, render.error_text())
        return
    state.record_turn(chat_state, text, final)
    await _safe_edit(
        sent,
        render.final_text(final),
        reply_markup=draft_keyboard(chat_state.draft is not None),
    )


_MAX_VOICE_SECONDS = 90  # bounds STT cost; Telegram supplies the duration
_STT_MIME_TYPES = {"audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/webm"}


def normalize_voice_mime(mime_type: str | None) -> str:
    """Telegram voice notes are audio/ogg; anything unrecognized falls back
    to that (the api schema rejects arbitrary strings)."""
    return mime_type if mime_type in _STT_MIME_TYPES else "audio/ogg"


@router.message(F.voice)
async def on_voice(message: Message, bot: Bot) -> None:
    """Voice-note ordering (Phase 7): download → api STT proxy (Groq Whisper,
    EN + Tamil auto-detect) → echo the redacted transcript → same agent turn
    as typed text. No reasoning here (Hard Rule 10)."""
    voice = message.voice
    if voice is None:
        return
    if (voice.duration or 0) > _MAX_VOICE_SECONDS:
        await message.answer(render.voice_too_long_text(_MAX_VOICE_SECONDS))
        return
    settings = get_settings()
    try:
        buffer = await bot.download(voice)
        audio = buffer.read() if buffer is not None else b""
    except Exception:  # noqa: BLE001 — Telegram file API hiccup → soft failure
        logger.warning("voice download failed (chat %s)", message.chat.id, exc_info=True)
        audio = b""
    if not audio:
        await message.answer(render.voice_failed_text())
        return
    result = await transcribe_voice(
        api_base_url=settings.api_base_url,
        internal_token=settings.internal_api_token,
        tg_user_id=message.from_user.id if message.from_user else message.chat.id,
        audio_base64=base64.b64encode(audio).decode(),
        mime_type=normalize_voice_mime(voice.mime_type),
    )
    if not result.ok or not (result.transcript or "").strip():
        logger.info("voice transcription unavailable: %s", result.detail)
        await message.answer(render.voice_failed_text())
        return
    await message.answer(render.voice_heard_text(result.transcript))
    await run_agent_turn(message, result.transcript)


@router.callback_query(F.data == "chat:place")
async def on_place(callback: CallbackQuery) -> None:
    settings = get_settings()
    chat_state = state.get_state(callback.message.chat.id) if callback.message else None
    items = state.draft_order_items(chat_state) if chat_state else []
    if not items or callback.message is None:
        await callback.answer("Nothing in your order yet!")
        return
    result = await place_order(
        api_base_url=settings.api_base_url,
        internal_token=settings.internal_api_token,
        tg_user_id=callback.from_user.id,
        items=items,
    )
    if result.ok:
        state.clear_draft(chat_state)
        await callback.message.answer(
            render.order_placed_text(result.order_id, result.total, settings.public_base_url)
        )
    else:
        await callback.message.answer(render.place_failed_text(result.detail))
    await callback.answer()


@router.callback_query(F.data.startswith("po:"))
async def on_po_decision(callback: CallbackQuery) -> None:
    """Owner tapped Approve/Reject on a PO card — forward to the api (which
    re-checks role + transition legality) and update the card in place."""
    parts = (callback.data or "").split(":")
    if len(parts) != 3 or parts[1] not in ("approve", "reject"):
        await callback.answer("Unsupported action")
        return
    settings = get_settings()
    result = await po_decision(
        api_base_url=settings.api_base_url,
        internal_token=settings.internal_api_token,
        tg_user_id=callback.from_user.id,
        po_id=int(parts[2]),
        action=parts[1],
    )
    if callback.message is not None:
        text = render.po_decided_text(int(parts[2]), result.status, result.detail)
        if result.ok:
            await _safe_edit(callback.message, text)  # buttons removed — decision is final
        else:
            await callback.message.answer(text)
    await callback.answer()


@router.callback_query(F.data == "chat:clear")
async def on_clear(callback: CallbackQuery) -> None:
    if callback.message is not None:
        state.clear_draft(state.get_state(callback.message.chat.id))
        await callback.message.answer(render.draft_cleared_text())
    await callback.answer()


@router.message()
async def on_unsupported(message: Message) -> None:
    await message.answer(render.unsupported_text())


async def healthz(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "bot", "version": "0.1.0"})


def make_po_notify_handler(
    bot: Bot, settings: Settings
) -> Callable[[web.Request], Awaitable[web.StreamResponse]]:
    """POST /internal/po-notify (api → bot): send an owner approval card.

    Guarded by the shared internal token — same trust boundary as bot→api.
    """

    async def po_notify(request: web.Request) -> web.Response:
        provided = request.headers.get("X-Internal-Token", "")
        if not settings.internal_api_token or not secrets.compare_digest(
            provided, settings.internal_api_token
        ):
            return web.json_response({"detail": "Forbidden"}, status=403)
        try:
            payload = await request.json()
            tg_user_id = int(payload["tg_user_id"])
            po_id = int(payload["po_id"])
        except (ValueError, KeyError):
            return web.json_response({"detail": "Bad payload"}, status=422)
        try:
            await bot.send_message(
                tg_user_id, render.po_notify_text(payload), reply_markup=po_keyboard(po_id)
            )
        except Exception:  # noqa: BLE001 — recipient may have blocked the bot
            logger.warning("po notify send failed (tg %s)", tg_user_id, exc_info=True)
            return web.json_response({"ok": False}, status=502)
        return web.json_response({"ok": True})

    return po_notify


def create_app(settings: Settings) -> web.Application:
    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    async def on_startup() -> None:
        await bot.set_webhook(
            settings.webhook_url,
            secret_token=settings.webhook_secret,
            drop_pending_updates=True,
        )
        logger.info("webhook set to %s", settings.webhook_url)

    dispatcher.startup.register(on_startup)

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.router.add_post("/internal/po-notify", make_po_notify_handler(bot, settings))
    SimpleRequestHandler(
        dispatcher=dispatcher, bot=bot, secret_token=settings.webhook_secret
    ).register(app, path=settings.webhook_path)
    setup_application(app, dispatcher, bot=bot)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    web.run_app(create_app(settings), host="0.0.0.0", port=settings.port)  # noqa: S104


if __name__ == "__main__":
    main()

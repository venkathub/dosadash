"""aiogram webhook entrypoint — Telegram adapter for the apps/ai order agent.

Webhook mode (not polling) per CLAUDE.md; Telegram posts updates to
PUBLIC_BASE_URL + /tg/webhook, protected by a secret token header.

Hard Rule 10: no reasoning here. The bot streams the agent's reply into a
progressively edited message (draft-edit streaming), renders the validated
draft, and forwards button taps to the api.
"""

import logging
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from dosadash_bot import render, state
from dosadash_bot.api_client import link_account, place_order, stream_chat
from dosadash_bot.config import Settings, get_settings

logger = logging.getLogger("dosadash_bot")

router = Router()

_EDIT_INTERVAL_SECONDS = 0.9  # Telegram edit rate — stay well under limits


class EditThrottle:
    """At most one message edit per interval (final edit is never throttled)."""

    def __init__(self, interval: float = _EDIT_INTERVAL_SECONDS) -> None:
        self._interval = interval
        self._last = 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self._last >= self._interval:
            self._last = now
            return True
        return False


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
    """One agent turn with draft-edit streaming: send a placeholder, edit it
    with reply text as tokens arrive, finish with draft + buttons."""
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
        message=message.text or "",
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
    state.record_turn(chat_state, message.text or "", final)
    await _safe_edit(
        sent,
        render.final_text(final),
        reply_markup=draft_keyboard(chat_state.draft is not None),
    )


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

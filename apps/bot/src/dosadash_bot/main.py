"""aiogram webhook entrypoint (Phase 0: /start + echo).

Webhook mode (not polling) per CLAUDE.md; Telegram posts updates to
PUBLIC_BASE_URL + /tg/webhook, protected by a secret token header.
"""

import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from dosadash_bot.config import Settings, get_settings
from dosadash_bot.render import echo_text, welcome_text

logger = logging.getLogger("dosadash_bot")

router = Router()


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    user = message.from_user
    await message.answer(welcome_text(user.first_name if user else None))


@router.message()
async def on_message(message: Message) -> None:
    await message.answer(echo_text(message.text))


async def healthz(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "bot", "version": "0.1.0"})


def create_app(settings: Settings) -> web.Application:
    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    async def on_startup(_dispatcher: Dispatcher) -> None:
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

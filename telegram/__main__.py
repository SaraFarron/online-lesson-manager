import asyncio
import logging

import httpx
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from telegram.api import BackendClient, CircuitBreaker
from telegram.config import BotConfig
from telegram.runtime import build_dispatcher


async def main() -> None:
    config = BotConfig()  # type: ignore[call-arg]
    bot = Bot(
        config.TOKEN.get_secret_value(), default=DefaultBotProperties(parse_mode="HTML")
    )
    async with httpx.AsyncClient(
        base_url=config.BACKEND_URL, timeout=config.REQUEST_TIMEOUT
    ) as http:
        client = BackendClient(
            http,
            config.SERVICE_CREDENTIAL.get_secret_value(),
            CircuitBreaker(config.FAILURE_THRESHOLD, config.RECOVERY_SECONDS),
        )
        dispatcher = build_dispatcher(client)
        try:
            await dispatcher.start_polling(bot, close_bot_session=False)
        finally:
            await bot.session.close()
            await dispatcher.storage.close()
            await dispatcher.fsm.events_isolation.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main())

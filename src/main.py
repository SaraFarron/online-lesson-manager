import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from core import logs
from core.config import Config, load_config
from core.menu import ALL_COMMANDS
from errors import add_errors
from logger import logger
from middlewares import LoggingMiddleware
from routers import all_routers


async def main():
    """Start bot."""
    logger.info(logs.START)
    config: Config = load_config()
    bot: Bot = Bot(token=config.tg_bot.token, default=DefaultBotProperties(parse_mode="HTML"))
    dp: Dispatcher = Dispatcher()

    dp = add_errors(dp)
    for router in all_routers:
        dp.include_router(router)

    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())

    await bot.set_my_commands(ALL_COMMANDS)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info(logs.STOP)

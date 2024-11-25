import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from commands import all_routers
from config import logs
from config.config import Config, load_config
from config.menu import ALL_COMMANDS
from logger import logger
from routers import all_routers as all_routers_new
from utils import delete_banned_users


async def main():
    """Start bot."""
    logger.info(logs.START)
    delete_banned_users()
    config: Config = load_config()
    bot: Bot = Bot(token=config.tg_bot.token, default=DefaultBotProperties(parse_mode="HTML"))
    dp: Dispatcher = Dispatcher()
    for router in all_routers:
        dp.include_router(router)

    for router in all_routers_new:
        dp.include_router(router)

    await bot.set_my_commands(ALL_COMMANDS)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info(logs.STOP)

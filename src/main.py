import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from config import logs
from config.config import Config, load_config
from general.handlers import router as general_router
from lessons.handlers import router as lessons_router
from logger import logger
from teacher.handlers import router as teacher_router


async def main():
    """Start bot."""
    logger.info(logs.START)
    config: Config = load_config()
    bot: Bot = Bot(token=config.tg_bot.token, default=DefaultBotProperties(parse_mode="HTML"))
    dp: Dispatcher = Dispatcher()
    dp.include_router(general_router)
    dp.include_router(lessons_router)
    dp.include_router(teacher_router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info(logs.STOP)

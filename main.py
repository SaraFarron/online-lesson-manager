import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from online_lesson_manager.config.config import Config, load_config
from online_lesson_manager.handlers import router
from online_lesson_manager.logger import logger


async def main():
    logger.info("Starting bot")
    config: Config = load_config()
    bot: Bot = Bot(token=config.tg_bot.token, default=DefaultBotProperties(parse_mode="HTML"))
    dp: Dispatcher = Dispatcher()
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")

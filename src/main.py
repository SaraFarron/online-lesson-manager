import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.scene import SceneRegistry
from aiogram.fsm.storage.memory import SimpleEventIsolation

from config import ALL_COMMANDS, Config, load_config
from log import logger, logs
from routers import all_routers
from scenes import all_scenes


async def main():
    """Start bot."""
    logger.info(logs.START)
    config: Config = load_config()
    bot: Bot = Bot(token=config.tg_bot.token, default=DefaultBotProperties(parse_mode="HTML"))
    dp: Dispatcher = Dispatcher(events_isolation=SimpleEventIsolation())
    for router in all_routers:
        dp.include_router(router)

    scene_registry = SceneRegistry(dp)
    for scene in all_scenes:
        scene_registry.add(scene)

    await bot.set_my_commands(ALL_COMMANDS)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info(logs.STOP)

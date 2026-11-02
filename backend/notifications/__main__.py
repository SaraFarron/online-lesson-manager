import asyncio
import logging
import signal

from backend.database import engine
from backend.notifications.homework_producers import produce_homework_reminders
from backend.notifications.runner import run_producer_runner
from backend.notifications.schedule_producers import produce_schedule_notifications

PRODUCERS = (produce_schedule_notifications, produce_homework_reminders)


async def main() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for sig in signals:
        loop.add_signal_handler(sig, stop_event.set)
    logger = logging.getLogger(__name__)
    logger.info("Notification producer runner started")
    try:
        await run_producer_runner(PRODUCERS, stop_event=stop_event)
    finally:
        for sig in signals:
            loop.remove_signal_handler(sig)
        await engine.dispose()
        logger.info("Notification producer runner stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

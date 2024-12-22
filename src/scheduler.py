import asyncio
from datetime import datetime, time

import aiojobs
from sqlalchemy.orm import Session

from config import logs
from config.config import TIMEZONE
from database import engine
from logger import logger
from messages import replies
from repositories import UserRepo
from service import Schedule
from utils import send_message


async def send_notifications(now: datetime):
    logger.info(logs.NOTIFICATIONS_START)
    with Session(engine) as db:
        notifies = set()
        users = UserRepo(db).all()
        schedule = Schedule(db)
        for user in users:
            text = schedule.lessons_day_message(user, now.date())
            if not text:
                continue
            notifies.add(user.username_dog)
            message = replies.LESSONS_ARE_COMING + text
            await send_message(user.telegram_id, message)
        logger.info(logs.NOTIFICATIONS_SENT, ", ".join(notifies))


async def lessons_notifications(timeout: float):
    """Send notifications about lessons."""
    now = datetime.now(TIMEZONE)
    if time(8, 15) <= now.time() < time(8, 20):
        await send_notifications(now)
    await asyncio.sleep(timeout)


async def start_scheduler():
    """Start scheduler."""
    timeout = 5 * 60
    logger.info(logs.SCHEDULER_START)
    async with aiojobs.Scheduler() as scheduler:
        while True:
            await scheduler.spawn(lessons_notifications(timeout))
            await asyncio.sleep(timeout)


if __name__ == "__main__":
    asyncio.run(start_scheduler())

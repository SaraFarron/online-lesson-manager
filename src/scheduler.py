import asyncio
from datetime import datetime, time

import aiojobs
from sqlalchemy.orm import Session

from core import logs
from core.config import TIMEZONE
from database import engine
from logger import logger
from src.models import User
from src.repositories import EventRepo
from utils import send_message


def notification(events: list, student_id: int | None):
    rows = []
    for event in events:
        pass
    if not rows:
        return None
    return "Скоро занятия:\n" + "\n".join(rows)


async def send_notifications(now: datetime):
    logger.info(logs.NOTIFICATIONS_START)
    with Session(engine) as db:
        notifies = set()
        users = db.query(User).all()
        repo = EventRepo(db)
        for user in users:
            student_id = user.id if user.role == User.Roles.STUDENT else None
            events = repo.day_schedule(user.executor_id, now.date(), student_id)
            text = notification(events, student_id)
            if not text:
                continue
            notifies.add(user.username_dog)
            await send_message(user.telegram_id, text)
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

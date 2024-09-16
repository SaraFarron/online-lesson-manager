import asyncio
from datetime import datetime

import aiojobs
from sqlalchemy.orm import Session

import messages
from config import logs
from config.config import TIMEZONE
from database import engine
from logger import logger
from models import User
from utils import TeacherSchedule, get_schedule, send_message


async def lessons_notifications(timeout: float):
    """Send notifications about lessons."""
    logger.info(logs.NOTIFICATIONS_START)
    if datetime.now(TIMEZONE).hour != 8:
        logger.info(logs.NO_NEED_TO_SEND)
        return
    with Session(engine) as session:
        now = datetime.now(TIMEZONE)
        notifees = []
        users = session.query(User).all()
        for user in users:
            schedule = get_schedule(user.telegram_id).schedule_day(datetime.now(TIMEZONE))
            text = []
            for s in schedule:
                lesson_start = datetime.now(TIMEZONE).replace(
                    hour=s[0].hour,
                    minute=s[0].minute,
                )
                if lesson_start > now:
                    if isinstance(schedule, TeacherSchedule):
                        text.append(messages.LESSON_IS_COMING_TEACHER % (s[2], s[0].strftime("%H:%M")))
                    else:
                        text.append(messages.LESSON_IS_COMING_STUDENT % s[0].strftime("%H:%M"))
                    if not notifees:
                        notifees.append(user.username_dog)
                    notifees.append(s[2])
            if text:
                message = messages.LESSONS_ARE_COMING + "%0A".join(text)
                await send_message(user.telegram_id, message)

            logger.info(logs.NOTIFICATIONS_SENT, ", ".join(notifees))
    await asyncio.sleep(timeout)


async def start_scheduler():
    """Start scheduler."""
    timeout = 3599
    logger.info(logs.SCHEDULER_START)
    async with aiojobs.Scheduler() as scheduler:
        while True:
            await scheduler.spawn(lessons_notifications(timeout))
            await asyncio.sleep(timeout)


if __name__ == "__main__":
    asyncio.run(start_scheduler())

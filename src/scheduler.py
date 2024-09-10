import asyncio
from datetime import datetime, timedelta

import aiojobs
from sqlalchemy.orm import Session

import messages
from config import logs
from config.config import TIMEZONE
from database import engine
from logger import logger
from models import Teacher, User
from utils import TeacherSchedule, send_message


async def lessons_notifications(timeout: float):
    """Send notifications about lessons."""
    logger.info(logs.NOTIFICATIONS_START)
    with Session(engine) as session:
        teachers = session.query(Teacher).all()
        for teacher in teachers:
            notifees = []
            user = session.query(User).filter(User.telegram_id == teacher.telegram_id).first()
            schedule = TeacherSchedule(user).schedule_day(datetime.now(TIMEZONE))
            for s in schedule:
                time_before_lesson = datetime.now(TIMEZONE).time() - s[0]
                if time_before_lesson <= timedelta(hours=1):
                    await send_message(
                        teacher.telegram_id,
                        messages.LESSON_IS_COMING_TEACHER % (s[2], s[0].strftime("%H:%M")),
                    )
                    await send_message(s[3], messages.LESSON_IS_COMING_STUDENT % s[0].strftime("%H:%M"))
                    if not notifees:
                        notifees.append(teacher.name)
                    notifees.append(s[2])
            logger.info(logs.NOTIFICATIONS_SENT, ", ".join(notifees))
    await asyncio.sleep(timeout)


async def start_scheduler():
    """Start scheduler."""
    timeout = 3600
    logger.info(logs.SCHEDULER_START)
    async with aiojobs.Scheduler() as scheduler:
        await scheduler.spawn(lessons_notifications(timeout))

        await asyncio.sleep(timeout)


if __name__ == "__main__":
    asyncio.run(start_scheduler())

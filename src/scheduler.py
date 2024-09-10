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
    if datetime.now(TIMEZONE).hour != 8:
        logger.info(logs.NO_NEED_TO_SEND)
        return
    with Session(engine) as session:
        teachers = session.query(Teacher).all()
        for teacher in teachers:
            notifees = []
            user = session.query(User).filter(User.telegram_id == teacher.telegram_id).first()
            now = datetime.now(TIMEZONE)
            schedule = TeacherSchedule(user).schedule_day(now)
            for s in schedule:
                lesson_start = datetime.now(TIMEZONE).replace(
                    hour=s[0].hour,
                    minute=s[0].minute,
                )
                # time_before_lesson = now - lesson_start
                #  and time_before_lesson <= timedelta(hours=12)
                if lesson_start > now:
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
    timeout = 1800
    logger.info(logs.SCHEDULER_START)
    async with aiojobs.Scheduler() as scheduler:
        while True:
            await scheduler.spawn(lessons_notifications(timeout))
            await asyncio.sleep(timeout)


if __name__ == "__main__":
    asyncio.run(start_scheduler())

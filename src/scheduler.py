import asyncio
from datetime import datetime, time

import aiojobs
from sqlalchemy.orm import Session

from config import logs
from config.config import TIMEZONE
from database import engine
from logger import logger
from messages import replies
from models import Teacher, User, Vacations
from service import SecondFunctions
from utils import send_message


async def send_notifications(now: datetime):
    """Send notifications about lessons."""
    logger.info(logs.NOTIFICATIONS_START)
    with Session(engine) as session:
        notifees = set()
        users = session.query(User).all()
        for user in users:
            vacations = (
                session.query(Vacations)
                .filter(
                    Vacations.start_date <= now.date(),
                    Vacations.end_date >= now.date(),
                    Vacations.teacher_id == user.teacher_id,
                )
                .all()
            )
            if vacations:
                logger.info(logs.NO_NOTIFICATIONS_ON_VACATION)
                return
            teacher: Teacher | None = session.query(Teacher).filter(Teacher.telegram_id == user.telegram_id).first()
            schedule = SecondFunctions(session, user if teacher else None)
            schedule = schedule.model_list_adapter_teacher(schedule.get_events_day(now))
            text = []
            for s in schedule:
                lesson_start = datetime.now(TIMEZONE).replace(
                    hour=s[0].hour,
                    minute=s[0].minute,
                )
                if lesson_start > now:
                    if teacher:
                        text.append(replies.LESSON_IS_COMING_TEACHER % (s[2], s[0].strftime("%H:%M")))
                    else:
                        text.append(replies.LESSON_IS_COMING_STUDENT % s[0].strftime("%H:%M"))
                    if not notifees:
                        notifees.add(user.username_dog)
                    notifees.add(s[2])
            if text:
                message = replies.LESSONS_ARE_COMING + "%0A".join(text)
                await send_message(user.telegram_id, message)

            logger.info(logs.NOTIFICATIONS_SENT, ", ".join(notifees))


async def lessons_notifications(timeout: float):
    """Send notifications about lessons."""
    now = datetime.now(TIMEZONE)
    if time(7, 15) <= now.time() < time(7, 20):
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

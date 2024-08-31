from __future__ import annotations

from datetime import datetime, timedelta

import aiohttp
from sqlalchemy.orm import Session

from src.config.base import getenv
from src.config.config import ADMINS, AVAILABLE_HOURS, DATE_FORMAT, DATE_FORMAT_HR, TIMEZONE
from src.database import engine
from src.models import Lesson


def get_weeks(start_date: datetime | None = None):
    """Get a list of the week days for the next 4 weeks."""
    if start_date is None:
        start_date = datetime.now(TIMEZONE)
    weeks = []
    for i in range(4):
        week_start = start_date + timedelta(days=i * 7)
        week = []
        for day in range(7):
            date = week_start + timedelta(days=day)
            weekday = date.strftime("%A").lower()
            week.append(
                {
                    "weekday": weekday,
                    "date_hr": date.strftime(DATE_FORMAT_HR),
                    "date": date.strftime(DATE_FORMAT),
                },
            )
        weeks.append(week)
    return weeks


def get_available_time(date: datetime) -> list[tuple[int, int]]:
    """Get a list of available time for the current day."""
    today_args = (date.year, date.month, date.day)
    with Session(engine) as session:
        # Get all lessons for the current day
        lessons = session.query(Lesson).filter(Lesson.date == date, Lesson.status == "upcoming").all()

        # Create a set of all times that are taken
        taken_times = [
            (
                datetime(*today_args, lesson.time.hour, lesson.time.minute, tzinfo=TIMEZONE),
                datetime(*today_args, lesson.end_time.hour, lesson.end_time.minute, tzinfo=TIMEZONE),
            )
            for lesson in lessons
        ]

        # Create a list of available times
        available_times = []
        for hour in AVAILABLE_HOURS:
            for minute in range(0, 60, 30):
                current_time = datetime(*today_args, hour, minute, tzinfo=TIMEZONE)
                taken = False
                for taken_time in taken_times:
                    if taken_time[0] <= current_time < taken_time[1]:
                        taken = True
                if not taken:
                    available_times.append((hour, minute))

    return available_times


def get_todays_schedule(date: datetime, user_id: int, telegram_id: int) -> list[dict[str, str]]:
    """Get lessons for the current day."""
    with Session(engine) as session:
        if telegram_id in ADMINS:
            lessons = session.query(Lesson).filter(Lesson.date == date.date()).all()
            schedule = "\n".join([f"{lesson.time}-{lesson.end_time}:{lesson.user.name}" for lesson in lessons])
        else:
            lessons = session.query(Lesson).filter(Lesson.date == date.date(), Lesson.user_id == user_id).all()
            schedule = "\n".join([f"{lesson.time} - {lesson.end_time}" for lesson in lessons])
    if not schedule:
        return "Today's schedule is empty"
    return "Today's schedule:\n" + schedule


async def send_message(telegram_id: int, message: str) -> None:
    """Send a message to the user."""
    token = getenv("BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={telegram_id}&text={message}"
    async with aiohttp.ClientSession() as session, session.get(url) as resp:
        await resp.text()


async def notify_admins(message: str) -> None:
    """Send a message to all admins."""
    for admin in ADMINS:
        await send_message(admin, message)

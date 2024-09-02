from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Literal

import aiohttp
from sqlalchemy.orm import Session

from config import messages
from config.base import getenv
from config.config import ADMINS, AVAILABLE_HOURS, DATE_FORMAT, DATE_FORMAT_HR, TIMEZONE, WORK_SCHEDULE_TIMETABLE_PATH
from database import engine
from models import Lesson


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


def working_hours() -> dict[str, str]:
    """Get working schedule."""
    with open(WORK_SCHEDULE_TIMETABLE_PATH) as f:
        data: dict[str, str] = json.load(f)
    days = {}
    for key, day in data.items():
        day_start = f"Начало: {day['start']}\n"
        day_break = f"Перерыв: {day['break']['start']} - {day['break']['end']}\n" if "break" in day else ""
        day_end = f"Конец: {day['end']}"
        days[key] = f"{day_start}{day_break}{day_end}"
    return days


def working_hours_on_day(weekday: Literal["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]):
    """Get working schedule for the current day."""
    with open(WORK_SCHEDULE_TIMETABLE_PATH) as f:
        data: dict[str, str] = json.load(f)
    if weekday in data:
        return {weekday: data[weekday]}
    return {weekday: messages.NO_DATA}


def get_available_hours(date: datetime) -> list[str]:
    """Get a list of available hours for the current day."""


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
                # Pasha asked to remove 30s from the list
                if minute == 30:
                    continue
                current_time = datetime(*today_args, hour, minute, tzinfo=TIMEZONE)
                taken = False
                for taken_time in taken_times:
                    if taken_time[0] <= current_time < taken_time[1]:
                        taken = True
                if not taken:
                    available_times.append((hour, minute))

    if (AVAILABLE_HOURS[-1], 30) in available_times:
        available_times.remove((AVAILABLE_HOURS[-1], 30))
    return available_times


def get_todays_schedule(date: datetime, user_id: int, telegram_id: int) -> list[dict[str, str]]:
    """Get lessons for the current day."""
    with Session(engine) as session:
        upcoming_today_filter_args = (Lesson.date == date.date(), Lesson.status == "upcoming")
        if telegram_id in ADMINS:
            lessons = session.query(Lesson).filter(*upcoming_today_filter_args).order_by(Lesson.time).all()
            schedule = "\n".join([f"{lesson.time}-{lesson.end_time}:{lesson.user.name}" for lesson in lessons])
        else:
            lessons = (
                session.query(Lesson)
                .filter(
                    *upcoming_today_filter_args,
                    Lesson.user_id == user_id,
                )
                .order_by(Lesson.time)
                .all()
            )
            schedule = "\n".join([f"{lesson.time} - {lesson.end_time}" for lesson in lessons])
    if not schedule:
        return messages.SCHEDULE_EMPTY
    return messages.SCHEDULE + schedule


def get_weeks_schedule(date: datetime, user_id: int, telegram_id: int) -> list[dict[str, str]]:
    """Get lessons for the current week."""
    with Session(engine) as session:
        upcoming_week_filter_args = (
            Lesson.date >= date.date(),
            Lesson.date < date.date() + timedelta(days=7),
            Lesson.status == "upcoming",
        )
        if telegram_id in ADMINS:
            lessons = session.query(Lesson).filter(*upcoming_week_filter_args).order_by(Lesson.date).all()
            schedule = "\n".join([f"{lesson.date} - {lesson.user.name}" for lesson in lessons])
        else:
            lessons = (
                session.query(Lesson)
                .filter(
                    *upcoming_week_filter_args,
                    Lesson.user_id == user_id,
                )
                .order_by(Lesson.date)
                .all()
            )
            schedule = "\n".join([f"{lesson.date}" for lesson in lessons])
    if not schedule:
        return messages.SCHEDULE_EMPTY_WEEK
    return messages.SCHEDULE_WEEK + schedule


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

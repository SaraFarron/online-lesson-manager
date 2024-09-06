from __future__ import annotations

from datetime import datetime
from typing import Iterable

import aiohttp
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.orm import Session

from config.base import getenv
from config.config import ADMINS, WEEKDAY_MAP
from database import engine
from models import Lesson, Reschedule, ScheduledLesson, Teacher


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


def inline_keyboard(buttons: dict[str, str] | Iterable[tuple[str, str]]):
    """Create an inline keyboard."""
    builder = InlineKeyboardBuilder()
    if isinstance(buttons, dict):
        for callback_data, text in buttons.items():
            builder.button(text=text, callback_data=callback_data)
    else:
        for text, callback_data in buttons:
            builder.button(text=text, callback_data=callback_data)
    return builder


def today_schedule_for_user(date: datetime, user_id: int):
    """Gets today's schedule for the user."""
    schedule = []
    weekday = WEEKDAY_MAP[date.weekday()]
    with Session(engine) as session:
        # Get regular lessons
        lessons = session.query(Lesson).filter(Lesson.date == date.date(), Lesson.user_id == user_id).all()
        schedule = [(lesson.time, lesson.end_time) for lesson in lessons]

        # Get reschedules
        reschedules = (
            session.query(Reschedule).filter(Reschedule.date == date.date(), Reschedule.user_id == user_id).all()
        )
        for reschedule in reschedules:
            schedule.append((reschedule.start_time, reschedule.end_time))
        canceled_sls = [rs.source for rs in reschedules]

        # Get scheduled lessons
        scheduled_lessons = (
            session.query(ScheduledLesson)
            .filter(ScheduledLesson.weekday == weekday, ScheduledLesson.user_id == user_id)
            .all()
        )
        for scheduled_lesson in scheduled_lessons:
            if scheduled_lesson not in canceled_sls:
                schedule.append((scheduled_lesson.start_time, scheduled_lesson.end_time))
    schedule.sort(key=lambda x: x[0])
    return schedule


def today_schedule_for_teacher(date: datetime, teacher: Teacher):
    """Gets today's schedule for the teacher."""
    schedule = []
    weekday = WEEKDAY_MAP[date.weekday()]
    with Session(engine) as session:
        students = [s.id for s in teacher.students]
        # Get regular lessons
        lessons = session.query(Lesson).filter(Lesson.date == date.date(), Lesson.user_id.in_(students)).all()
        schedule = [(lesson.time, lesson.end_time, lesson.user.name) for lesson in lessons]

        # Get reschedules
        reschedules = (
            session.query(Reschedule).filter(Reschedule.date == date.date(), Reschedule.user_id.in_(students)).all()
        )
        for reschedule in reschedules:
            schedule.append((reschedule.start_time, reschedule.end_time, reschedule.user.name))
        canceled_sls = [rs.source for rs in reschedules]

        # Get scheduled lessons
        scheduled_lessons = (
            session.query(ScheduledLesson)
            .filter(ScheduledLesson.weekday == weekday, ScheduledLesson.user_id.in_(students))
            .all()
        )
        for scheduled_lesson in scheduled_lessons:
            if scheduled_lesson not in canceled_sls:
                schedule.append((scheduled_lesson.start_time, scheduled_lesson.end_time, scheduled_lesson.user.name))
    schedule.sort(key=lambda x: x[0])
    return schedule

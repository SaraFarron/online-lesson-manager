from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, time, timedelta
from itertools import chain
from typing import Iterable

import aiohttp
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.orm import Session

from config.base import getenv
from config.config import ADMINS, TIMEZONE
from database import engine
from models import Reschedule, RestrictedTime, ScheduledLesson, User

MAX_HOUR = 23


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


def inline_keyboard(buttons: dict[str | int, str] | Iterable[tuple[str, str | int]]):
    """Create an inline keyboard."""
    builder = InlineKeyboardBuilder()
    if isinstance(buttons, dict):
        for callback_data, text in buttons.items():
            builder.button(text=text, callback_data=callback_data)
    else:
        for text, callback_data in buttons:
            builder.button(text=text, callback_data=callback_data)
    return builder


def this_week():
    """Get dates for the current week."""
    today = datetime.now(TIMEZONE)
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=7)
    return [start_of_week + timedelta(n) for n in range(int((end_of_week - start_of_week).days))]


class BaseSchedule(ABC):
    def __init__(self, user: User) -> None:
        """Base schedule class containing basic methods."""
        self.user = user
        self.filter_by_user = False

    def available_time(self, taken_times: list[tuple[time, time]]) -> list[time]:
        """Get available times without taken time."""
        available = []
        current_time: time = self.user.teacher.work_start
        while current_time < self.user.teacher.work_end:
            taken = False
            for taken_time in taken_times:
                if taken_time[0] <= current_time < taken_time[1]:
                    taken = True
                    break
            if not taken:
                available.append(current_time)
            current_time = (
                current_time.replace(hour=current_time.hour + 1)
                if current_time.hour < MAX_HOUR
                else current_time.replace(hour=0)
            )
        return available

    @abstractmethod
    def schedule_day(self, day: datetime) -> list[tuple[time, time]]:  # noqa: D102
        pass

    @abstractmethod
    def schedule_weekday(self, weekday: int) -> list[tuple[time, time]]:  # noqa: D102
        pass

    def available_time_day(self, day: datetime) -> list[time]:
        """Free time for the day."""
        return self.available_time(self.schedule_day(day))

    def available_time_weekday(self, weekday: int) -> list[time]:
        """Free time for the weekday."""
        return self.available_time(self.schedule_weekday(weekday))

    def restrictions(self, session: Session, date_or_weekday: datetime | int):
        """Get restrictions for the date or weekday."""
        if isinstance(date_or_weekday, int):
            filters = [RestrictedTime.weekday == date_or_weekday, RestrictedTime.user == self.user]
        else:
            filters = [RestrictedTime.weekday == date_or_weekday.weekday(), RestrictedTime.user == self.user]
        if not self.filter_by_user:
            filters.pop(-1)
        return session.query(RestrictedTime).filter(*filters).all()

    def reschedules(self, session: Session, date: datetime):
        """Get reschedules for the date."""
        return session.query(Reschedule).filter(Reschedule.date == date.date()).all()

    def scheduled_lessons(self, session: Session, date_or_wekday: datetime | int):
        """Get scheduled lessons for the date or weekday."""
        if isinstance(date_or_wekday, int):
            filters = [ScheduledLesson.weekday == date_or_wekday, ScheduledLesson.user == self.user]
        else:
            filters = [ScheduledLesson.weekday == date_or_wekday.weekday(), ScheduledLesson.user == self.user]
        if not self.filter_by_user:
            filters.pop(-1)
        return session.query(ScheduledLesson).filter(*filters).all()


class TeacherSchedule(BaseSchedule):
    def schedule_day(self, day: datetime) -> list[tuple[time, time, str]]:
        """Schedule for the day."""
        with Session(engine) as session:
            return list(
                chain(
                    [
                        (lesson.start_time, lesson.end_time, lesson.user.name)
                        for lesson in self.scheduled_lessons(session, day)
                    ],
                    [
                        (reschedule.start_time, reschedule.end_time, reschedule.user.name)
                        for reschedule in self.reschedules(session, day)
                    ],
                ),
            )

    def schedule_weekday(self, weekday: int) -> list[tuple[time, time, str]]:
        """Schedule for the weekday."""
        with Session(engine) as session:
            return list(
                chain(
                    [
                        (lesson.start_time, lesson.end_time, lesson.user.name)
                        for lesson in self.scheduled_lessons(session, weekday)
                    ],
                ),
            )


class StudentSchedule(BaseSchedule):
    def __init__(self, user: User) -> None:  # noqa: D107
        super().__init__(user)
        self.filter_by_user = True

    def schedule_day(self, day: datetime) -> list[tuple[time, time]]:
        """Schedule for the day."""
        with Session(engine) as session:
            return list(
                chain(
                    [(lesson.start_time, lesson.end_time) for lesson in self.scheduled_lessons(session, day)],
                    [(reschedule.start_time, reschedule.end_time) for reschedule in self.reschedules(session, day)],
                ),
            )

    def schedule_weekday(self, weekday: int) -> list[tuple[time, time]]:
        """Schedule for the weekday."""
        with Session(engine) as session:
            return list(
                chain(
                    [(lesson.start_time, lesson.end_time) for lesson in self.scheduled_lessons(session, weekday)],
                ),
            )

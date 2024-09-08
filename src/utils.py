from __future__ import annotations

from datetime import datetime, timedelta
from itertools import chain
from typing import Iterable, Literal

import aiohttp
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.orm import Session

from config import config
from config.base import getenv
from config.config import ADMINS, TIMEZONE, WEEKDAY_MAP
from database import engine
from logger import logger
from models import Lesson, Reschedule, RestrictedTime, ScheduledLesson, Teacher, User

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


def this_week():
    """Get dates for the current week."""
    today = datetime.now(TIMEZONE)
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=7)
    return [start_of_week + timedelta(n) for n in range(int((end_of_week - start_of_week).days))]


def possible_time_for_user(
    user_telegram_id: int,
    weekday: Literal["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"],
) -> list[str]:
    """Return a list of available times."""
    result = []
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == user_telegram_id).first()
        if not user:
            logger.warning("NO USER %s", user_telegram_id)
            return []

        # Check if any restrictions for this day
        restriced_periods = (
            session.query(RestrictedTime)
            .filter(
                RestrictedTime.weekday == weekday,
                RestrictedTime.user == user,
            )
            .all()
        )
        taken_times = [(period.start_time, period.end_time) for period in restriced_periods]

        # Check if any lessons for this day
        lessons_this_day = session.query(ScheduledLesson).filter(ScheduledLesson.weekday == weekday).all()
        for lesson in lessons_this_day:
            taken_times.append((lesson.start_time, lesson.end_time))  # noqa: PERF401

        # Forming buttons for available time
        current_time: datetime = user.teacher.work_start
        while current_time < user.teacher.work_end:
            taken = False
            for taken_time in taken_times:
                if taken_time[0] <= current_time < taken_time[1]:
                    taken = True
                    break
            if not taken:
                result.append(current_time.strftime("%H.%M"))
            current_time = (
                current_time.replace(hour=current_time.hour + 1)
                if current_time.hour < MAX_HOUR
                else current_time.replace(hour=0)
            )

    return result


class StudentSchedule:
    """Student schedule."""

    def __init__(self, user: User) -> None:
        """Initialize."""
        self.user = user
        with Session(engine) as session:
            self.teacher: Teacher = session.query(Teacher).get(user.teacher_id)
            self.teacher_weekends = [w.weekday for w in self.teacher.weekends]

    def restrictions(self, session: Session, date_or_weekday: datetime | int):
        """Get restrictions for this date or weekday."""
        if isinstance(date_or_weekday, int):
            return (
                session.query(RestrictedTime)
                .filter(
                    RestrictedTime.weekday == date_or_weekday,
                    RestrictedTime.user == self.user,
                )
                .all()
            )
        return (
            session.query(RestrictedTime)
            .filter(
                RestrictedTime.weekday == date_or_weekday.weekday(),
                RestrictedTime.user == self.user,
            )
            .all()
        )

    def reschedules(self, session: Session, date: datetime):
        """Get reschedules for this date."""
        return (
            session.query(Reschedule)
            .filter(
                Reschedule.date == date.date(),
            )
            .all()
        )

    def scheduled_lessons(self, session: Session, date_or_wekday: datetime | int):
        """Get scheduled lessons for this date or weekday."""
        if isinstance(date_or_wekday, int):
            return (
                session.query(ScheduledLesson)
                .filter(
                    ScheduledLesson.weekday == date_or_wekday,
                )
                .all()
            )
        return (
            session.query(ScheduledLesson)
            .filter(
                ScheduledLesson.weekday == date_or_wekday.weekday(),
            )
            .all()
        )

    def lessons(self, session: Session, date: datetime):
        """Get lessons for this date."""
        return (
            session.query(Lesson)
            .filter(
                Lesson.date == date.date(),
            )
            .all()
        )

    def lessons_this_date(self, date: datetime):
        """Get lessons for this date."""
        weekday = WEEKDAY_MAP[date.weekday()]
        with Session(engine) as session:
            # Get regular lessons
            lessons = (
                session.query(Lesson)
                .filter(
                    Lesson.date == date.date(),
                    Lesson.user_id == self.user.id,
                )
                .all()
            )

            # Get reschedules
            reschedules = (
                session.query(Reschedule)
                .filter(
                    Reschedule.date == date.date(),
                    Reschedule.user_id == self.user.id,
                )
                .all()
            )

            # Get scheduled lessons
            sls = (
                session.query(ScheduledLesson)
                .filter(
                    ScheduledLesson.weekday == weekday,
                    ScheduledLesson.user_id == self.user.id,
                )
                .all()
            )
        return lessons, reschedules, sls

    # def lessons_this_weekday(self, weekday: int):
    #     """Get lessons for this weekday."""

    def available_weekdays(self) -> list[str]:
        """Get available weekdays."""
        result = []
        with Session(engine) as session:
            for weekday in config.WEEKDAYS:
                # Check if any restrictions for this day
                restriced_periods = (
                    session.query(RestrictedTime)
                    .filter(
                        RestrictedTime.weekday == weekday,
                        RestrictedTime.user == self.user,
                    )
                    .all()
                )

                if weekday in self.teacher_weekends:
                    continue
                if any(period.whole_day_restricted for period in restriced_periods):
                    continue
                result.append(weekday)
            return result

    def available_dates(self, start: datetime, end: datetime):
        """Get available dates."""
        result = []
        dates = []
        current_day = start
        while current_day < end:
            if current_day.weekday() not in self.available_weekdays():
                continue
            dates.append(current_day)
            current_day = current_day + timedelta(days=1)
        for day in dates:
            available_time = self.available_times_for_date(day)
            if available_time:
                result.append(day)
        return day

    def available_time(self, taken_times: list[tuple[datetime, datetime]]) -> list[datetime]:
        """Get available times without taken time."""
        available = []
        current_time: datetime = self.user.teacher.work_start
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

    def available_times_for_date(self, date: datetime) -> list[datetime]:
        """Get available times for this date."""
        with Session(engine) as session:
            # Collect all unavailable times for this day
            taken_times = chain(
                [(period.start_time, period.end_time) for period in self.restrictions(session, date)],
                [(lesson.start_time, lesson.end_time) for lesson in self.scheduled_lessons(session, date)],
                [(reschedule.start_time, reschedule.end_time) for reschedule in self.reschedules(session, date)],
            )
        return self.available_time(taken_times)

    def available_times_for_weekday(self, weekday: int):
        """Get available times for this weekday."""
        with Session(engine) as session:
            # Collect all unavailable times for this day
            taken_times = chain(
                [(period.start_time, period.end_time) for period in self.restrictions(session, weekday)],
                [(lesson.start_time, lesson.end_time) for lesson in self.scheduled_lessons(session, weekday)],
            )
        return self.available_time(taken_times)

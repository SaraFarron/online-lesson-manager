from __future__ import annotations

from datetime import datetime, time, timedelta
from itertools import chain
from typing import Iterable

import aiohttp
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.orm import Session

from config.base import getenv
from config.config import ADMINS, TIMEZONE
from database import engine
from models import Reschedule, RestrictedTime, ScheduledLesson, Teacher, User, WorkBreak

MAX_HOUR = 23


def get_teacher():
    """Get the right teacher from the database."""
    with Session(engine) as session:
        teacher = session.query(Teacher).filter(Teacher.telegram_id == ADMINS[0]).first()
        if not teacher:
            teacher = session.query(Teacher).filter(Teacher.telegram_id == ADMINS[1]).first()
    return teacher


async def send_message(telegram_id: int, message: str) -> None:
    """Send a message to the user."""
    token = getenv("BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={telegram_id}&text={message}&parse_mode=HTML"
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


def daterange(start: datetime, end: datetime, step: int = 1) -> Iterable[datetime]:
    """Get dates between two dates."""
    date_range = []
    current_date = start

    while current_date <= end:
        date_range.append(current_date)
        current_date += timedelta(days=step)

    return date_range


def get_user(telegram_id: int):
    """Get user by telegram id."""
    with Session(engine) as session:
        return session.query(User).filter(User.telegram_id == telegram_id).first()


def get_schedule(telegram_id: int):
    """Get schedule by telegram id."""
    with Session(engine) as session:
        if session.query(Teacher).filter(Teacher.telegram_id == telegram_id).first():
            return TeacherSchedule(get_user(telegram_id))
        return StudentSchedule(get_user(telegram_id))


def get_cancellations_day(session: Session, day: datetime, user: User | None = None):
    """Get cancellations from the database."""
    if user is None:
        return session.query(Reschedule).filter(Reschedule.source_date == day.date()).all()
    return session.query(Reschedule).filter(Reschedule.source_date == day.date(), Reschedule.user_id == user.id).all()


def get_events_day(session: Session, day: datetime, user: User | None = None):
    """Get events from the database."""
    sl_query = session.query(ScheduledLesson)
    rs_query = session.query(Reschedule)
    cancellations = [x.source_id for x in get_cancellations_day(session, day, user)]

    if user:
        sl_query = sl_query.filter(ScheduledLesson.user_id == user.id)
        rs_query = rs_query.filter(Reschedule.user_id == user.id)

    sl_query = sl_query.filter(ScheduledLesson.weekday == day.weekday(), ScheduledLesson.id.not_in(cancellations))
    rs_query = rs_query.filter(Reschedule.date == day.date())

    return list(chain(sl_query.all(), rs_query.all()))


def get_events_weekday(session: Session, weekday: int, user: User | None = None):
    """Get events from the database."""
    sl_query = session.query(ScheduledLesson)
    rs_query = session.query(Reschedule)

    if user:
        sl_query = sl_query.filter(ScheduledLesson.user_id == user.id)
        rs_query = rs_query.filter(Reschedule.user_id == user.id)

    sl_query = sl_query.filter(ScheduledLesson.weekday == weekday)

    return list(chain(sl_query.all(), rs_query.all()))


def get_events(session: Session, day_or_weekday: datetime | int, user: User | None = None):
    """Get events from the database."""
    if isinstance(day_or_weekday, datetime):
        return get_events_day(session, day_or_weekday, user)
    return get_events_weekday(session, day_or_weekday, user)


def model_list_adapter_user(models: list[ScheduledLesson | Reschedule | RestrictedTime | WorkBreak]):
    """Convert list of models to list of dicts."""
    result = [model.edges for model in models if model.edges[0]]
    result.sort(key=lambda x: x[0])
    return result

def model_list_adapter_teacher(models: list[ScheduledLesson | Reschedule | RestrictedTime | WorkBreak]):
    """Convert list of models to list of dicts."""
    result = [(*model.edges, model.user.username_dog, model.user.telegram_id) for model in models]
    result.sort(key=lambda x: x[0])
    return result


def get_avaiable_time(start: time, end: time, taken_times: list[tuple[time, time]]):
    """Get available time from start to end without taken times."""
    available = []
    current_time: time = start
    while current_time < end:
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


def get_unavailable_weekdays(user_id: int):
    """Get unavailable weekdays."""
    with Session(engine) as session:
        user: User | None = session.query(User).get(user_id)
        if user is None:
            return []
        weekends = [w.weekday for w in user.teacher.weekends]
        restricted = [r.weekday for r in user.restricted_times if r.whole_day_restricted]
        return weekends + restricted

def get_available_weekdays(session: Session, user: User):
    """Get available weekdays."""
    teacher: Teacher = session.query(Teacher).get(user.teacher_id)
    na_weekdays = get_unavailable_weekdays(user.id)
    result = []
    for wd in range(7):
        if wd in na_weekdays:
            continue
        events = get_events_weekday(session, wd, None)
        if get_avaiable_time(teacher.work_start, teacher.work_end, model_list_adapter_user(events)):
            result.append(wd)
    return result


def get_available_days(session: Session, user: User) -> list[datetime]:
    """Get available days."""
    teacher: Teacher = session.query(Teacher).get(user.teacher_id)
    na_weekdays = [w.weekday for w in teacher.weekends] + [
        r.weekday for r in user.restricted_times if r.whole_day_restricted
    ]
    result = []
    for wd in range(7):
        if wd in na_weekdays:
            continue
        if get_avaiable_time(teacher.work_start, teacher.work_end, get_events_day(session, wd, None)):
            result.append(wd)
    return result


class TeacherSchedule:
    def __init__(self, user: User) -> None:
        """Base schedule class containing basic methods."""
        self.user = user

    def schedule_day(self, day: datetime):
        """Get schedule for the day."""
        with Session(engine) as session:
            events = get_events_day(session, day)
            return model_list_adapter_teacher(events)

    def available_weekdays(self):
        """Get available weekdays."""
        with Session(engine) as session:
            return get_available_weekdays(session, self.user)

    def available_time_weekday(self, weekday: int):
        """Get available time for the weekday."""
        with Session(engine) as session:
            events = get_events_weekday(session, weekday)
            teacher: Teacher = session.query(Teacher).get(self.user.teacher_id)
            return get_avaiable_time(
                teacher.work_start,
                teacher.work_end,
                model_list_adapter_teacher(events),
            )

    def available_time_day(self, day: datetime):
        """Get available time for the day."""
        with Session(engine) as session:
            events = get_events_day(session, day)
            teacher: Teacher = session.query(Teacher).get(self.user.teacher_id)
            return get_avaiable_time(
                teacher.work_start,
                teacher.work_end,
                model_list_adapter_teacher(events),
            )


class StudentSchedule:
    def __init__(self, user: User) -> None:
        """Base schedule class containing basic methods."""
        self.user = user

    def schedule_day(self, day: datetime):
        """Get schedule for the day."""
        with Session(engine) as session:
            return model_list_adapter_user(get_events_day(session, day, self.user))

    def available_weekdays(self):
        """Get available weekdays."""
        with Session(engine) as session:
            return get_available_weekdays(session, self.user)

    def available_time_weekday(self, weekday: int):
        """Get available time for the weekday."""
        with Session(engine) as session:
            events = get_events_weekday(session, weekday)
            teacher: Teacher = session.query(Teacher).get(self.user.teacher_id)
            return get_avaiable_time(
                teacher.work_start,
                teacher.work_end,
                model_list_adapter_user(events),
            )

    def available_time_day(self, day: datetime):
        """Get available time for the day."""
        with Session(engine) as session:
            events = get_events_day(session, day)
            teacher: Teacher = session.query(Teacher).get(self.user.teacher_id)
            return get_avaiable_time(
                teacher.work_start,
                teacher.work_end,
                model_list_adapter_user(events),
            )

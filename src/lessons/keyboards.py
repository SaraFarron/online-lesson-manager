from __future__ import annotations

from datetime import datetime
from typing import Literal

from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.orm import Session

from config.config import MAX_BUTTON_ROWS, WEEKDAYS
from database import engine
from lessons.callbacks import (
    CreateScheduledLessonCallBack,
    DateCallBack,
    RemoveLessonCallBack,
    TimeCallBack,
    WeekdayCallback,
    YesNoCallBack,
)
from lessons.utils import get_available_time, get_weekday, get_weeks
from models import Lesson, ScheduledLesson


def weekdays_keyboard():
    """Create a keyboard with weekdays."""
    builder = InlineKeyboardBuilder()
    for short, long in WEEKDAYS.items():
        builder.button(text=long, callback_data=WeekdayCallback(weekday=short))
    builder.adjust(1 if len(list(builder.buttons)) <= MAX_BUTTON_ROWS else 2, repeat=True)
    return builder.as_markup()


def available_schedule_keyboard(weekday: Literal["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]):
    """Create a keyboard with available schedule for the current day."""
    work_schedule = get_weekday(weekday)
    work_start = datetime.strptime(work_schedule["start"], "%H:%M")  # noqa: DTZ007
    work_end = datetime.strptime(work_schedule["end"], "%H:%M")  # noqa: DTZ007
    available_hours = list(range(work_start.hour, work_end.hour))
    if "break" in work_schedule:
        break_start = datetime.strptime(work_schedule["break"]["start"], "%H:%M")  # noqa: DTZ007
        break_end = datetime.strptime(work_schedule["break"]["end"], "%H:%M")  # noqa: DTZ007
        for h in available_hours:
            if break_start.hour <= h < break_end.hour:
                available_hours.remove(h)
    with Session(engine) as session:
        lessons = session.query(ScheduledLesson).filter(ScheduledLesson.weekday == weekday).all()
    if lessons:
        for h in available_hours:
            for lesson in lessons:
                if lesson.time.hour <= h < lesson.end_time.hour:
                    available_hours.remove(h)
                    break
    builder = InlineKeyboardBuilder()
    for hour in available_hours:
        builder.button(
            text=f"{hour:02}:00",
            callback_data=CreateScheduledLessonCallBack(
                weekday=weekday,
                start_hour=hour,
            ),
        )
    builder.adjust(1 if len(list(builder.buttons)) <= MAX_BUTTON_ROWS else 2, repeat=True)
    return builder.as_markup()


def calendar_keyboard():
    """Create a calendar keyboard for the current month."""
    builder = InlineKeyboardBuilder()

    weekdays = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
    for day in weekdays:
        builder.button(text=day, callback_data=day)

    for week in get_weeks():
        for day in week:
            builder.button(text=day["date_hr"], callback_data=DateCallBack(date=day["date"]))

    builder.adjust(7, repeat=True)
    return builder.as_markup()


def available_time_keyboard(date: datetime):
    """Create a keyboard with available time for the day."""
    builder = InlineKeyboardBuilder()
    available_times = get_available_time(date)
    if not available_times:
        return None
    for hour, minute in available_times:
        # Separator symbol ':' can not be used in callback_data
        if minute == 0:
            builder.button(text=f"{hour:02}:00", callback_data=TimeCallBack(time=f"{hour:02}.00"))
        else:
            builder.button(text=f"{hour:02}:{minute:02}", callback_data=TimeCallBack(time=f"{hour:02}.{minute:02}"))
    builder.adjust(1 if len(list(builder.buttons)) <= MAX_BUTTON_ROWS else 2, repeat=True)
    return builder.as_markup()


def lessons_to_remove_keyboard(lessons: list[Lesson]):
    """Create a keyboard with available lessons to remove."""
    builder = InlineKeyboardBuilder()
    for lesson in lessons:
        text = f"{lesson.date} {lesson.time}-{lesson.end_time}"
        builder.button(text=text, callback_data=RemoveLessonCallBack(lesson_id=lesson.id))
    builder.adjust(1 if len(list(builder.buttons)) <= MAX_BUTTON_ROWS else 2, repeat=True)
    return builder.as_markup()


def yes_no_keyboard():
    """Create a keyboard with yes and no buttons."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Yes", callback_data=YesNoCallBack(answer="yes"))
    builder.button(text="No", callback_data=YesNoCallBack(answer="no"))
    builder.adjust(2)
    return builder.as_markup()

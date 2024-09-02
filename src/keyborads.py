from __future__ import annotations

from datetime import datetime
from typing import Literal

from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from callbacks import DateCallBack, RemoveLessonCallBack, TimeCallBack, WeekdayCallBack, YesNoCallBack, EditWeekdayCallBack
from config import help
from config.config import ADMINS, MAX_BUTTON_ROWS
from models import Lesson
from utils import get_available_time, get_weeks, working_hours, working_hours_on_day


def available_commands(user_id: int):
    """Create a keyboard with available commands."""
    builder = ReplyKeyboardBuilder()
    builder.button(text=f"{help.START}")
    builder.button(text=f"{help.HELP}")
    builder.button(text=f"{help.ADD_LESSON}")
    builder.button(text=f"{help.REMOVE_LESSON}")
    builder.button(text=f"{help.GET_SCHEDULE}")
    builder.button(text=f"{help.GET_SCHEDULE_WEEK}")
    builder.button(text=f"{help.CANCEL}")
    if user_id in ADMINS:
        builder.button(text=help.EDIT_WORKING_HOURS)
        builder.button(text=help.ADMIN_GROUP)
    builder.adjust(2, repeat=True)
    return builder.as_markup()


def calendar():
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


def available_time(date: datetime):
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


def lessons_to_remove(lessons: list[Lesson]):
    """Create a keyboard with available lessons to remove."""
    builder = InlineKeyboardBuilder()
    for lesson in lessons:
        text = f"{lesson.date} {lesson.time}-{lesson.end_time}"
        builder.button(text=text, callback_data=RemoveLessonCallBack(lesson_id=lesson.id))
    builder.adjust(1 if len(list(builder.buttons)) <= MAX_BUTTON_ROWS else 2, repeat=True)
    return builder.as_markup()


def yes_no():
    """Create a keyboard with yes and no buttons."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Yes", callback_data=YesNoCallBack(answer="yes"))
    builder.button(text="No", callback_data=YesNoCallBack(answer="no"))
    builder.adjust(2)
    return builder.as_markup()


def working_hours_keyboard():
    """Create a keyboard with working hours."""
    builder = InlineKeyboardBuilder()
    for weekday, schedule in working_hours().items():
        builder.button(text=f"{weekday}\n{schedule}", callback_data=WeekdayCallBack(weekday=weekday))
    builder.adjust(1 if len(list(builder.buttons)) <= MAX_BUTTON_ROWS else 2, repeat=True)
    return builder.as_markup()


def working_hours_on_day_keyboard(weekday: Literal["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]):
    """Create a keyboard with working hours on the current day."""
    builder = InlineKeyboardBuilder()
    data = working_hours_on_day(weekday)[weekday]
    builder.button(text="Начало: " + data["start"], callback_data=EditWeekdayCallBack(period="daystart"))
    if "break" in data:
        start_text, end_text = "Начало перерыва: " + data["break"]["start"], "Конец перерыва: " + data["break"]["end"]
        builder.button(text=start_text, callback_data=EditWeekdayCallBack(period="breakstart"))
        builder.button(text=end_text, callback_data=EditWeekdayCallBack(period="breakend"))
        builder.button(text="Убрать перерыв", callback_data=EditWeekdayCallBack(period="rmbreak"))
    else:
        builder.button(text="Добавить перерыв", callback_data=EditWeekdayCallBack(period="addbreak"))
    builder.button(text="Конец: " + data["end"], callback_data=EditWeekdayCallBack(period="dayend"))
    builder.adjust(1 if len(list(builder.buttons)) <= MAX_BUTTON_ROWS else 2, repeat=True)
    return builder.as_markup()

from datetime import datetime

from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from src.callbacks import DateCallBack, RemoveLessonCallBack, TimeCallBack, YesNoCallBack
from src.config.config import ADMINS, MAX_BUTTON_ROWS
from src.models import Lesson
from src.utils import get_available_time, get_weeks


def available_commands(user_id: int):
    """Create a keyboard with available commands."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="/start - show welcoming message")
    builder.button(text="/help - show this message")
    builder.button(text="/add_lesson - add a lesson")
    builder.button(text="/remove_lesson - remove or reschedule a lesson")
    builder.button(text="/get_schedule - get schedule for today")
    if user_id in ADMINS:
        builder.button(text="this just shows, that you are in admin group")
    builder.adjust(2, repeat=True)
    return builder.as_markup()


def calendar():
    """Create a calendar keyboard for the current month."""
    builder = InlineKeyboardBuilder()

    weekdays = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
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

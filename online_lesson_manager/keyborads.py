from datetime import datetime
from aiogram.utils.keyboard import InlineKeyboardBuilder

from online_lesson_manager.utils import get_weeks, get_available_time


def calendar():
    """Create a calendar keyboard for the current month."""
    builder = InlineKeyboardBuilder()

    weekdays = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    for day in weekdays:
        builder.button(text=day, callback_data=f"set:{day}")

    for week in get_weeks():
        for day in week:
            builder.button(text=day["date"], callback_data=f"set:{day}")

    builder.adjust(7, repeat=True)
    return builder.as_markup()


def available_time(date: datetime):
    """Create a keyboard with available time for the day."""
    builder = InlineKeyboardBuilder()
    available_times = get_available_time(date)
    if not available_times:
        return None
    for hour, minute in available_times:
        if minute == 0:
            builder.button(text=f"{hour:02}:00", callback_data=f"set:{hour}:00")
        else:
            builder.button(text=f"{hour:02}:{minute:02}", callback_data=f"set:{hour}:{minute}")
    return builder.as_markup()

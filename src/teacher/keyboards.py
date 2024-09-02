from __future__ import annotations

from typing import Literal

from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.config import MAX_BUTTON_ROWS
from teacher.callbacks import EditWeekdayCallBack, WeekdayCallBack
from teacher.utils import working_hours, working_hours_on_day


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

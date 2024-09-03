from typing import Literal

from aiogram.filters.callback_data import CallbackData


class DateCallBack(CallbackData, prefix="choose_date"):
    date: str


class TimeCallBack(CallbackData, prefix="choose_time"):
    time: str


class RemoveLessonCallBack(CallbackData, prefix="remove_lesson"):
    lesson_id: int


class YesNoCallBack(CallbackData, prefix="yes_no"):
    answer: str


class WeekdayCallback(CallbackData, prefix="choose_weekday"):
    weekday: Literal["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]


class CreateScheduledLessonCallBack(CallbackData, prefix="create_scheduled_lesson"):
    weekday: Literal["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
    start_hour: int

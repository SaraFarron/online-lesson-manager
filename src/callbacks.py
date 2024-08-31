from aiogram.filters.callback_data import CallbackData


class DateCallBack(CallbackData, prefix="choose_date"):
    date: str


class TimeCallBack(CallbackData, prefix="choose_time"):
    time: str


class RemoveLessonCallBack(CallbackData, prefix="remove_lesson"):
    lesson_id: int


class YesNoCallBack(CallbackData, prefix="yes_no"):
    answer: str

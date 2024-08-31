from aiogram.filters.callback_data import CallbackData


class DateCallBack(CallbackData, prefix="choose_date"):
    date: str


class TimeCallBack(CallbackData, prefix="choose_time"):
    time: str

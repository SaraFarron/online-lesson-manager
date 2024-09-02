from aiogram.filters.callback_data import CallbackData


class WeekdayCallBack(CallbackData, prefix="choose_weekday"):
    weekday: str


class EditWeekdayCallBack(CallbackData, prefix="edit_weekday"):
    period: str

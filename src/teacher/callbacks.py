from aiogram.filters.callback_data import CallbackData


class WorkWeekdayCallBack(CallbackData, prefix="choose_work_weekday"):
    weekday: str


class EditWeekdayCallBack(CallbackData, prefix="edit_weekday"):
    period: str

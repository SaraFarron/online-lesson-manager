from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder


def all_commands(role: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    match role:
        case "admin":
            buttons = [
                "Расписание учителя",
            ]
        case "teacher":
            buttons = [
                "Расписание на сегодня",
                "Расписание на неделю",
                "Рабочее время",
                "Перерывы",
                "Проверить расписание",
                "Рассылка всем ученикам",
                "Ученики",
                "Расписание каникул",
            ]
        case "student":
            buttons = [
                "Добавить урок",
                "Добавить разовый урок",
                "Отменить/перенести урок",
                "Расписание на сегодня",
                "Расписание на неделю",
                "Расписание каникул",
            ]
        case _:
            buttons = []
    for button in buttons:
        builder.button(text=button)
    builder.adjust(2, repeat=True)
    return builder.as_markup()

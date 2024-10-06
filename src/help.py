from enum import Enum


class Commands(Enum):
    START = "Приветственное сообщение"
    HELP = "Помощь"
    ADD_SCHEDULED_LESSON = "Добавить урок в расписание"
    RESCHEDULE = "Отменить/перенести урок"
    TODAY_SCHEDULE = "Расписание на сегодня"
    WEEK_SCHEDULE = "Расписание на неделю"
    CANCEL = "Отмена"


class AdminCommands(Enum):
    ADMIN_GROUP = "О, админ!"
    EDIT_WORKING_HOURS = "Редактировать рабочие часы"
    CHECK_SCHEDULE = "Проверить расписание"
    SEND_TO_EVERYONE = "Рассылка всем ученикам"

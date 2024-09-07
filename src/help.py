from enum import Enum


class Commands(Enum):
    START = "Приветственное сообщение"
    HELP = "Помощь"
    CANCEL = "Отмена"
    TODAY_SCHEDULE = "Расписание на сегодня"
    WEEK_SCHEDULE = "Расписание на неделю"
    ADD_SCHEDULED_LESSON = "Добавить урок в расписание"
    RESCHEDULE = "Отменить урок"
    ADD_LESSON = "Добавить урок на дату"


class AdminCommands(Enum):
    ADMIN_GROUP = "О, админ!"
    EDIT_WORKING_HOURS = "Редактировать рабочие часы"

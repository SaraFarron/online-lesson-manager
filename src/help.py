from enum import Enum


class Commands(Enum):
    START = "Приветственное сообщение"
    HELP = "Помощь"
    CANCEL = "Отмена"
    GET_SCHEDULE = "Расписание на сегодня"
    GET_SCHEDULE_WEEK = "Расписание на неделю"
    ADD_SCHEDULED_LESSON = "Добавить урок в расписание"
    REMOVE_LESSON = "Отменить урок"
    ADD_LESSON = "Добавить урок на дату"
    CREATE_SCHEDULED_LESSON = "Создать расписание"


class AdminCommands(Enum):
    ADMIN_GROUP = "О, админ!"
    EDIT_WORKING_HOURS = "Редактировать рабочие часы"

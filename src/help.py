from enum import Enum


class Commands(Enum):
    ADD_SCHEDULED_LESSON = "Добавить урок"
    ADD_ONE_LESSON = "Добавить разовый урок"
    RESCHEDULE = "Отменить/перенести урок"
    TODAY_SCHEDULE = "Расписание на сегодня"
    WEEK_SCHEDULE = "Расписание на неделю"
    VACATIONS = "Расписание каникул"


class AdminCommands(Enum):
    EDIT_WORKING_HOURS = "Редактировать рабочие часы"
    CHECK_SCHEDULE = "Проверить расписание"
    SEND_TO_EVERYONE = "Рассылка всем ученикам"

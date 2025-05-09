from enum import Enum

ADD_LESSON = "Добавить урок на дату"
REMOVE_LESSON = "Отменить урок"
GET_SCHEDULE = "Расписание на сегодня"
GET_SCHEDULE_WEEK = "Расписание на неделю"
EDIT_WORKING_HOURS = "Редактировать рабочие часы"
ADD_RECURRENT_LESSON = "Добавить урок в расписание"
CREATE_SCHEDULED_LESSON = "Создать расписание"


class Commands(Enum):
    ADD_RECURRENT_LESSON = "Добавить урок"
    ADD_LESSON = "Добавить разовый урок"
    MOVE_LESSON = "Отменить/перенести урок"
    TODAY_SCHEDULE = "Расписание на сегодня"
    WEEK_SCHEDULE = "Расписание на неделю"
    VACATIONS = "Расписание каникул"


class AdminCommands(Enum):
    EDIT_WORKING_HOURS = "Редактировать рабочие часы"
    CHECK_SCHEDULE = "Проверить расписание"
    SEND_TO_EVERYONE = "Рассылка всем ученикам"

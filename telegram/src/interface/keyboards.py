from enum import Enum


class Commands(Enum):
    ADD_RECURRENT_LESSON = "Добавить урок"
    ADD_LESSON = "Добавить разовый урок"
    MOVE_LESSON = "Отменить/перенести урок"
    DAY_SCHEDULE = "Расписание на сегодня"
    WEEK_SCHEDULE = "Расписание на неделю"
    VACATIONS = "Расписание каникул"
    # CHOOSE_HOMEWORK = "Домашние задания"  # noqa: ERA001


class AdminCommands(Enum):
    DAY_SCHEDULE = "Расписание на сегодня"
    WEEK_SCHEDULE = "Расписание на неделю"
    MANAGE_WORK_HOURS = "Рабочее время"
    WORK_BREAKS = "Перерывы"
    CHECK_OVERLAPS = "Проверить расписание"
    SEND_TO_EVERYONE = "Рассылка всем ученикам"
    STUDENTS = "Ученики"
    VACATIONS = "Расписание каникул"
    # CHOOSE_HOMEWORK = "Домашние задания"  # noqa: ERA001

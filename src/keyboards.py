from collections.abc import Iterable
from datetime import date, datetime, timedelta
from enum import Enum

from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from core.config import DATE_FMT, DB_DATETIME, MAX_BUTTON_ROWS, TIME_FMT, WEEKDAY_MAP
from src.models import Event, RecurrentEvent, User


class Commands(Enum):
    ADD_RECURRENT_LESSON = "Добавить урок"
    ADD_LESSON = "Добавить разовый урок"
    MOVE_LESSON = "Отменить/перенести урок"
    DAY_SCHEDULE = "Расписание на сегодня"
    WEEK_SCHEDULE = "Расписание на неделю"
    VACATIONS = "Расписание каникул"


class AdminCommands(Enum):
    DAY_SCHEDULE = "Расписание на сегодня"
    WEEK_SCHEDULE = "Расписание на неделю"
    MANAGE_WORK_HOURS = "Рабочее время"
    CHECK_SCHEDULE = "Проверить расписание"
    SEND_TO_EVERYONE = "Рассылка всем ученикам"
    STUDENTS = "Ученики"


class Keyboards:
    @classmethod
    def inline_keyboard(cls, buttons: dict[str, str] | Iterable[tuple[str, str]], as_markup=True):
        """Create an inline keyboard."""
        builder = InlineKeyboardBuilder()
        if isinstance(buttons, dict):
            for callback_data, text in buttons.items():
                builder.button(text=text, callback_data=callback_data)
        else:
            for text, callback_data in buttons:
                builder.button(text=text, callback_data=callback_data)
        adjust = len(buttons) // MAX_BUTTON_ROWS
        builder.adjust(1 if not adjust else adjust, repeat=True)
        if as_markup:
            return builder.as_markup()
        return builder

    @classmethod
    def choose_week(cls, current_monday: date, callback: str):
        previous_week_start = datetime.strftime(current_monday - timedelta(days=7), DATE_FMT)
        next_week_start = datetime.strftime(current_monday + timedelta(days=7), DATE_FMT)
        buttons = {
            callback + previous_week_start: "Предыдущая неделя",
            callback + next_week_start: "Следующая неделя",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def choose_lesson_type(cls, recurrent_type_callback: str, single_type_callback: str):
        buttons = {
            recurrent_type_callback: "Еженедельное занятие",
            single_type_callback: "Одноразовое занятие",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def weekdays(cls, days: list[int], callback: str, short=False):
        buttons = {}
        for day in days:
            match day:
                case 0: buttons[callback + "0"] = "ПН" if short else "Понедельник"
                case 1: buttons[callback + "1"] = "ВТ" if short else "Вторник"
                case 2: buttons[callback + "2"] = "СР" if short else "Среда"
                case 3: buttons[callback + "3"] = "ЧТ" if short else "Четверг"
                case 4: buttons[callback + "4"] = "ПТ" if short else "Пятница"
                case 5: buttons[callback + "5"] = "СБ" if short else "Суббота"
                case 6: buttons[callback + "6"] = "ВС" if short else "Воскресенье"
        return cls.inline_keyboard(buttons)

    @classmethod
    def choose_time(cls, times: list[datetime], callback: str):
        times = [datetime.strftime(t, TIME_FMT) for t in times]
        buttons = {callback + str(t): str(t) for t in times}
        return cls.inline_keyboard(buttons)

    @classmethod
    def choose_lesson(cls, lessons: list[tuple], callback: str):
        buttons = {}
        for lesson in lessons:
            lesson_datetime = datetime.strptime(lesson[0], DB_DATETIME)
            lesson_date = datetime.strftime(lesson_datetime, DATE_FMT)
            lesson_weekday = WEEKDAY_MAP[lesson_datetime.weekday()]["short"]
            lesson_time = datetime.strftime(lesson_datetime, TIME_FMT)
            if lesson[3] == RecurrentEvent.EventTypes.LESSON:
                buttons[callback + "re" + str(lesson[-1])] = f"{lesson[3]} {lesson_weekday} в {lesson_time}"
            elif lesson[3] in (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON):
                buttons[callback + "e" + str(lesson[-1])] = f"{lesson[3]} {lesson_date} в {lesson_time}"
            else:
                continue
        return cls.inline_keyboard(buttons)

    @classmethod
    def move_or_delete(cls, callback: str):
        buttons = {
            callback + "move": "Перенести",
            callback + "delete": "Отменить",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def once_or_forever(cls, callback: str):
        buttons = {
            callback + "forever": "Навсегда",
            callback + "once": "На одну дату",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def check_notify(cls, callback: str):
        buttons = {
            "Отправить сообщения": callback + "send",
            "Отмена": callback + "cancel",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def all_commands(cls, role: User.Roles):
        """Create a keyboard with available commands."""
        builder = ReplyKeyboardBuilder()
        match role:
            case User.Roles.STUDENT:
                for command in Commands:
                    builder.button(text=command.value)
            case User.Roles.TEACHER:
                for command in AdminCommands:
                    builder.button(text=command.value)
            case _: raise Exception("message", "", "Unknown role")
        builder.adjust(2, repeat=True)
        return builder.as_markup()

    @classmethod
    def work_hours(cls, events: list, weekends: list, callback: str, callback2: str):
        buttons = {}
        events_types = [e.event_type for e in events]
        if RecurrentEvent.EventTypes.WORK_START in events_types:
            start = [datetime.strptime(e.end, DB_DATETIME) for e in events if e.event_type == RecurrentEvent.EventTypes.WORK_START][0]
            buttons[callback + "delete_start"] = f"Удалить начало в {datetime.strftime(start, TIME_FMT)}"
        else:
            buttons[callback + "add_start"] = "Добавить начало"
        if RecurrentEvent.EventTypes.WORK_END in events_types:
            end = [datetime.strptime(e.start, DB_DATETIME) for e in events if e.event_type == RecurrentEvent.EventTypes.WORK_END][0]
            buttons[callback + "delete_end"] = f"Удалить конец в {datetime.strftime(end, TIME_FMT)}"
        else:
            buttons[callback + "add_end"] = "Добавить конец"

        for weekend in weekends:
            if not isinstance(weekend.start, datetime):
                start = datetime.strptime(weekend.start, DB_DATETIME)
            else:
                start = weekend.start
            weekday = WEEKDAY_MAP[start.weekday()]["long"]
            buttons[callback2 + f"delete_weekend/{weekend.id}"] = f"Удалить выходной в {weekday}"
        buttons[callback2 + "add_weekend"] = "Добавить выходной"

        return cls.inline_keyboard(buttons)

    @classmethod
    def vacations(cls, events: list, callback: str):
        buttons = {}
        for e in events:
            event = f"{datetime.strftime(e.start, DATE_FMT)} - {datetime.strftime(e.end, DATE_FMT)}"
            buttons[callback + f"delete_vacation/{e.id}"] = f"Удалить каникулы {event}"
        buttons[callback + "add_vacation"] = "Добавить каникулы"
        return cls.inline_keyboard(buttons)

    @classmethod
    def users(cls, users: list[User], callback: str):
        buttons = {}
        for user in users:
            buttons[callback + str(user.id)] = f"Профиль {user.username}"
        return cls.inline_keyboard(buttons)

    @classmethod
    def profile(cls, user_id: int, callback: str):
        buttons = {
            callback + str(user_id): "Удалить пользователя (запросит подтверждение)"
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def confirm(cls, callback: str):
        buttons = {
            callback + "yes": "Да",
            callback + "no": "Нет",
        }
        return cls.inline_keyboard(buttons)
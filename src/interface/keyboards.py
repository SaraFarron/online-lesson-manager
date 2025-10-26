from collections.abc import Iterable
from datetime import date, datetime, timedelta
from enum import Enum
from math import ceil

from aiogram.types.inline_keyboard_markup import InlineKeyboardMarkup
from aiogram.types.reply_keyboard_markup import ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from src.core.config import CHANGE_DELTA, DATE_FMT, DB_DATETIME, MAX_BUTTON_ROWS, SHORT_DATE_FMT, TIME_FMT, WEEKDAY_MAP
from src.db.models import Event, RecurrentEvent, User
from src.db.schemas import EventSchema, RecurrentEventSchema


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


class Keyboards:
    @classmethod
    def inline_keyboard(
        cls,
        buttons: dict[str, str] | Iterable[tuple[str, str]],
        as_markup: bool=True,
        ) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        """Create an inline keyboard."""
        if not buttons:
            return None
        builder = InlineKeyboardBuilder()
        if isinstance(buttons, dict):
            for callback_data, text in buttons.items():
                builder.button(text=text, callback_data=callback_data)
        else:
            for text, callback_data in buttons:
                builder.button(text=text, callback_data=callback_data)
        adjust = ceil(len(buttons) / MAX_BUTTON_ROWS)
        builder.adjust(adjust if adjust else 1, repeat=True)
        if as_markup:
            return builder.as_markup()
        return builder

    @classmethod
    def choose_week(cls, current_monday: date, callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        previous_week_start = datetime.strftime(current_monday - timedelta(days=7), DATE_FMT)
        next_week_start = datetime.strftime(current_monday + timedelta(days=7), DATE_FMT)
        buttons = {
            callback + previous_week_start: "Предыдущая неделя",
            callback + next_week_start: "Следующая неделя",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def choose_lesson_type(
        cls,
        recurrent_type_callback: str,
        single_type_callback: str,
        ) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {
            recurrent_type_callback: "Еженедельное занятие",
            single_type_callback: "Одноразовое занятие",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def weekdays(
        cls,
        days: list[int],
        callback: str, short: bool=False,
        ) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
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
                case _: continue
        return cls.inline_keyboard(buttons)

    @classmethod
    def choose_time(cls, times: list[datetime], callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        times_str = [datetime.strftime(t, TIME_FMT) for t in times]
        buttons = {callback + str(t): str(t) for t in times_str}
        return cls.inline_keyboard(buttons)

    @classmethod
    def choose_lesson(
        cls,
        lessons: list[RecurrentEventSchema | EventSchema],
        callback: str,
        ) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {}
        now = datetime.now()
        threshold = now + CHANGE_DELTA
        for lesson in lessons:
            lesson_datetime = lesson.start
            if isinstance(lesson, EventSchema) and threshold > lesson_datetime:
                continue
            lesson_date = datetime.strftime(lesson_datetime, SHORT_DATE_FMT)
            lesson_weekday = WEEKDAY_MAP[lesson_datetime.weekday()]["short"]
            lesson_time = datetime.strftime(lesson_datetime, TIME_FMT)
            if lesson.event_type == RecurrentEvent.EventTypes.LESSON and isinstance(lesson, RecurrentEventSchema):
                buttons[callback + "re" + str(lesson.id)] = f"{lesson.event_type} в {lesson_weekday} {lesson_time}"
            elif lesson.event_type == Event.EventTypes.MOVED_LESSON:
                buttons[callback + "e" + str(lesson.id)] = f"{lesson.event_type} {lesson_date} в {lesson_time}"
            elif lesson.event_type == Event.EventTypes.LESSON and isinstance(lesson, EventSchema):
                buttons[callback + "e" + str(lesson.id)] = f"Разовый {lesson.event_type} {lesson_date} в {lesson_time}"
            else:
                continue
        return cls.inline_keyboard(buttons)

    @classmethod
    def move_or_delete(cls, callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {
            callback + "move": "Перенести",
            callback + "delete": "Отменить",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def once_or_forever(cls, callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {
            callback + "forever": "Навсегда",
            callback + "once": "На одну дату",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def check_notify(cls, callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {
            "Отправить сообщения": callback + "send",
            "Отмена": callback + "cancel",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def all_commands(cls, role: User.Roles) -> ReplyKeyboardMarkup:
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
    def work_hours(
        cls,
        events: list,
        weekends: list,
        callback: str,
        callback2: str,
        ) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {}
        events_types = [e.event_type for e in events]
        if RecurrentEvent.EventTypes.WORK_START in events_types:
            start = next(e.end for e in events if e.event_type == RecurrentEvent.EventTypes.WORK_START)
            buttons[callback + "delete_start"] = f"Удалить начало в {datetime.strftime(start, TIME_FMT)}"
        else:
            buttons[callback + "add_start"] = "Добавить начало"
        if RecurrentEvent.EventTypes.WORK_END in events_types:
            end = next(e.start for e in events if e.event_type == RecurrentEvent.EventTypes.WORK_END)
            buttons[callback + "delete_end"] = f"Удалить конец в {datetime.strftime(end, TIME_FMT)}"
        else:
            buttons[callback + "add_end"] = "Добавить конец"

        for weekend in weekends:
            weekday = WEEKDAY_MAP[weekend.start.weekday()]["long"]
            buttons[callback2 + f"delete_weekend/{weekend.id}"] = f"Удалить выходной в {weekday}"
        buttons[callback2 + "add_weekend"] = "Добавить выходной"

        return cls.inline_keyboard(buttons)

    @classmethod
    def vacations(cls, events: list, callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {}
        for e in events:
            start = datetime.strptime(e.start, DB_DATETIME) if isinstance(e.start, str) else e.start
            end = datetime.strptime(e.end, DB_DATETIME) if isinstance(e.end, str) else e.end
            event = f"{datetime.strftime(start, SHORT_DATE_FMT)} - {datetime.strftime(end, SHORT_DATE_FMT)}"
            buttons[callback + f"delete_vacation/{e.id}"] = f"Удалить каникулы {event}"
        buttons[callback + "add_vacation"] = "Добавить каникулы"
        return cls.inline_keyboard(buttons)

    @classmethod
    def users(cls, users: list[User], callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {}
        for user in users:
            buttons[callback + str(user.id)] = user.username if user.username else user.full_name
        return cls.inline_keyboard(buttons)

    @classmethod
    def profile(cls, user_id: int, callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {
            callback + str(user_id): "Удалить пользователя (запросит подтверждение)",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def confirm(cls, callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {
            callback + "yes": "Да",
            callback + "no": "Нет",
        }
        return cls.inline_keyboard(buttons)

    @classmethod
    def work_breaks(
        cls,
        events: list,
        add_callback: str,
        remove_callback: str,
        ) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {}
        for event in events:
            duration = datetime.strftime(event.start, TIME_FMT) + " - " + datetime.strftime(event.end, TIME_FMT)
            weekday = WEEKDAY_MAP[event.start.weekday()]["short"]
            buttons[remove_callback + str(event.id)] = f"Удалить Перерыв {weekday} {duration}"
        buttons[add_callback] = "Добавить перерыв"
        return cls.inline_keyboard(buttons)

    @classmethod
    def send_messages(cls, callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        return cls.inline_keyboard({callback: "Отправить сообщения ученикам"})

    @classmethod
    def homeworks(cls, homeworks: list, callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {}
        for hw in homeworks:
            buttons[callback + str(hw.id)] = str(hw)
        return cls.inline_keyboard(buttons)

    @classmethod
    def hw_actions(cls, callback: str) -> InlineKeyboardMarkup | InlineKeyboardBuilder | None:
        buttons = {
            callback + "get": "Посмотреть ДЗ",
            callback + "send": "Сдать ДЗ",
        }
        return cls.inline_keyboard(buttons)

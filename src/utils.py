from datetime import datetime, time
from os import getenv

import aiohttp
from aiogram.types import CallbackQuery, Message

from src.core.config import DATE_FMT, TIME_FMT, WEEKDAY_MAP
from src.models import Event, RecurrentEvent, User

MAX_HOUR = 23


class RouterConf:
    scene = ""
    command = "/" + scene


def telegram_checks(event: Message | CallbackQuery):
    if isinstance(event, Message):
        if not event.from_user:
            raise Exception("message", "Ошибка на стороне telegram", "event.from_user is False")
        return event
    if not isinstance(event.message, Message):
        raise Exception("message", "Ошибка на стороне telegram", "event.message is not Message")
    return event.message


def parse_date(text: str):
    for fmt in ("%Y-%m-%d", "%Y %m %d", "%Y.%m.%d"):
        try:
            date = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return date
    return None


def parse_time(text: str):
    for fmt in ("%H:%M", "%H %M"):
        try:
            time = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return time
    return None


def get_callback_arg(callback_data: str, callback: str):
    return callback_data.replace(callback, "")


def calc_end_time(time: time):
    """Calculate end time."""
    return time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0)


async def send_message(telegram_id: int, message: str) -> None:
    """Send a message to the user."""
    token = getenv("BOT_TOKEN")
    message = message.replace("\n", "%0A")
    url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={telegram_id}&text={message}&parse_mode=HTML"
    async with aiohttp.ClientSession() as session, session.get(url) as resp:
        await resp.text()


def day_schedule_text(lessons: list, users_map: dict, user: User):
    result = []
    for lesson in lessons:
        if lesson[3] in (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON):
            dt = lesson[0]
            lesson_str = f"{lesson[3]} {datetime.strftime(dt, DATE_FMT)} в {datetime.strftime(dt, TIME_FMT)}"
        elif lesson[3] == RecurrentEvent.EventTypes.LESSON:
            dt = lesson[0]
            weekday = WEEKDAY_MAP[dt.weekday()]["short"]
            lesson_str = f"{lesson[3]} {weekday} в {datetime.strftime(dt, TIME_FMT)}"
        else:
            continue
        if user.role == User.Roles.TEACHER:
            lesson_str += f"у {users_map[lesson[2]]}"
        result.append(lesson_str)
    return result

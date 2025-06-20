from datetime import datetime, time
from os import getenv

import aiohttp
from aiogram.types import CallbackQuery, Message

from src.core.config import SHORT_DATE_FMT, TIME_FMT
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


def parse_date(text: str, in_future=False):
    for fmt in (
            SHORT_DATE_FMT,
            SHORT_DATE_FMT.replace(".", " "),
            SHORT_DATE_FMT.replace(".", "-"),
            "%Y-%m-%d",
            "%Y %m %d",
            "%Y.%m.%d",
    ):
        try:
            date = datetime.strptime(text, fmt)
        except ValueError:
            continue
        now = datetime.now()
        if date.year < now.year:
            if in_future and date.replace(year=now.year) <= now:
                return date.replace(year=now.year + 1)
            return date.replace(year=now.year)
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
        response = await resp.text()
    if '"ok":false' in response:
        print(f"tg_id:{telegram_id}\nmessage:{message}\nresponse:{response}")


def day_schedule_text(lessons: list, users_map: dict, user: User):
    result = []
    for lesson in lessons:
        if lesson[3] in (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON) or lesson[3] == RecurrentEvent.EventTypes.LESSON:
            dt = lesson[0]
            if not isinstance(lesson[-1], bool) and lesson[3] == Event.EventTypes.LESSON:
                lesson_str = f"Разовый урок в {datetime.strftime(dt, TIME_FMT)}"
            else:
                lesson_str = f"{lesson[3]} в {datetime.strftime(dt, TIME_FMT)}"
        else:
            continue
        if user.role == User.Roles.TEACHER:
            lesson_str += f" у {users_map[lesson[2]]}"
        result.append(lesson_str)
    return result

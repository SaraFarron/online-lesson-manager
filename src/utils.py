from datetime import datetime, time, timedelta
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
    event_types = [Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON, RecurrentEvent.EventTypes.LESSON]
    if user.role == User.Roles.TEACHER:
        event_types.append(Event.EventTypes.WORK_BREAK)
    for lesson in lessons:
        if lesson[3] in event_types:
            dt = lesson[0]
            if not isinstance(lesson[-1], bool) and lesson[3] == Event.EventTypes.LESSON:
                lesson_str = f"Разовый урок в {datetime.strftime(dt, TIME_FMT)}"
            else:
                lesson_str = f"{lesson[3]} в {datetime.strftime(dt, TIME_FMT)}"
        else:
            continue
        if user.role == User.Roles.TEACHER and lesson[3] != Event.EventTypes.WORK_BREAK:
            lesson_str += f" у {users_map[lesson[2]]}"
        result.append(lesson_str)
    return result


def find_three_lessons_block(events):
    events = sorted(events, key=lambda x: x[0])
    consecutive = 0
    block_end_time = None
    found_no_slot = False

    for i, event in enumerate(events):
        start, end, _, event_type, *_ = event

        if event_type in ("Урок", "Перенос"):
            if consecutive == 0:
                consecutive = 1
            else:
                prev_start, prev_end, _, prev_type, *_ = events[i - 1]
                if prev_type in ("Урок", "Перенос") and prev_end == start:
                    consecutive += 1
                else:
                    consecutive = 1
            block_end_time = end
        else:
            consecutive = 0

        if consecutive >= 3:
            next_index = i + 1
            if next_index >= len(events):
                found_no_slot = True
                continue

            next_event = events[next_index]
            next_start, _, _, next_type, *_ = next_event

            # Если сразу после блока перерыв — ничего делать не нужно
            if next_type == "Перерыв":
                return False

            # Если есть слот в 15 минут
            if next_start - block_end_time >= timedelta(minutes=15):
                return block_end_time
            else:
                found_no_slot = True

    if found_no_slot:
        return "Перерыв не был поставлен автоматически, т.к. слот после уроков меньше 15 минут"
    return False

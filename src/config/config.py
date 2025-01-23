from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path

import pytz
from dotenv import load_dotenv
from pydantic import BaseModel

from config.base import getenv


@dataclass
class TelegramBotConfig:
    token: str


@dataclass
class Config:
    tg_bot: TelegramBotConfig


def load_config() -> Config:
    """Parse a `.env` file and load the variables into environment variables."""
    load_dotenv()

    return Config(tg_bot=TelegramBotConfig(token=getenv("BOT_TOKEN")))


DATE_FORMAT = "%d.%m.%Y"
DATE_FORMAT_HR = "%d.%m"
TIME_FORMAT = "%H.%M"
TIMEZONE = pytz.timezone("Europe/Moscow")

MAX_BUTTON_ROWS = 10

ADMINS = {
    "irina_gambal": int(getenv("IRINA_TG_ID")),
    "sara_farron": int(getenv("SARA_TG_ID")),
}

WORK_START = time(hour=9, minute=0, tzinfo=TIMEZONE)
WORK_END = time(hour=21, minute=0, tzinfo=TIMEZONE)
HRS_TO_CANCEL = 3

WORK_SCHEDULE_TIMETABLE_PATH = Path(__file__).parent.parent.parent / "db/work_schedule.json"
WEEKDAYS = {
    "ПН": "Понедельник",
    "ВТ": "Вторник",
    "СР": "Среда",
    "ЧТ": "Четверг",
    "ПТ": "Пятница",
    "СБ": "Суббота",
    "ВС": "Воскресенье",
}
WEEKDAY_MAP = {
    0: "ПН",
    1: "ВТ",
    2: "СР",
    3: "ЧТ",
    4: "ПТ",
    5: "СБ",
    6: "ВС",
}
WEEKDAY_MAP_FULL = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье",
}

BANNED_USERS = [5224132707, 5138705886, 6435412623, 1279494544, 568291561, 1101945040, 1690341677]


class Weekday(BaseModel):
    number: int
    short: str
    long: str


WEEKDAYS_MODEL = [
    Weekday(number=0, short="ПН", long="Понедельник"),
    Weekday(number=1, short="ВТ", long="Вторник"),
    Weekday(number=2, short="СР", long="Среда"),
    Weekday(number=3, short="ЧТ", long="Четверг"),
    Weekday(number=4, short="ПТ", long="Пятница"),
    Weekday(number=5, short="СБ", long="Суббота"),
    Weekday(number=6, short="ВС", long="Воскресенье"),
]

MAX_HOUR = 23

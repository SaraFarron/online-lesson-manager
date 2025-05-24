from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path

import pytz
from dotenv import load_dotenv
from pydantic import BaseModel

from src.core.base import getenv


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


BOT_TOKEN = getenv("BOT_TOKEN")
DATE_FORMAT = "%d.%m.%Y"
DATE_FORMAT_HR = "%d.%m"
TIME_FORMAT = "%H.%M"
TIMEZONE = pytz.timezone("Europe/Moscow")

MAX_BUTTON_ROWS = 8

WORK_START = time(hour=9, minute=0, tzinfo=TIMEZONE)
WORK_END = time(hour=21, minute=0, tzinfo=TIMEZONE)
HRS_TO_CANCEL = 3

WORK_SCHEDULE_TIMETABLE_PATH = Path(__file__).parent.parent.parent / "db/work_schedule.json"


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

WEEKDAY_MAP = {
    0: {"long": "Понедельник", "short": "ПН"},
    1: {"long": "Вторник", "short": "ВТ"},
    2: {"long": "Среда", "short": "СР"},
    3: {"long": "Четверг", "short": "ЧТ"},
    4: {"long": "Пятница", "short": "ПТ"},
    5: {"long": "Суббота", "short": "СБ"},
    6: {"long": "Воскресенье", "short": "ВС"},
}

MAX_HOUR = 23

# New

TIME_FMT = "%H:%M"
DATE_FMT = "%Y.%m.%d"
DATETIME_FMT = "%Y.%m.%d %H:%M"
DB_DATETIME = "%Y-%m-%d %H:%M:%S.%f"
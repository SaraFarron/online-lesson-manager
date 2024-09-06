from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

import pytz
from dotenv import load_dotenv

from config.base import getenv


@dataclass
class TelegramBotConfig:
    token: str


@dataclass
class Config:
    tg_bot: TelegramBotConfig


def load_config() -> Config:
    """Parse a `.env` file and load the variables into environment valriables."""
    load_dotenv()

    return Config(tg_bot=TelegramBotConfig(token=getenv("BOT_TOKEN")))


DATE_FORMAT = "%d.%m.%Y"
DATE_FORMAT_HR = "%d.%m"
TIME_FORMAT = "%H.%M"
TIMEZONE = pytz.timezone("Europe/Moscow")

MAX_BUTTON_ROWS = 10

ADMINS = [
    5362724893,  # Pasha
    882315246,  # Sara
]
WORK_START = time(hour=9, minute=0, tzinfo=TIMEZONE)
WORK_END = time(hour=21, minute=0, tzinfo=TIMEZONE)

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

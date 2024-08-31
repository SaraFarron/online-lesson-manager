from dataclasses import dataclass

import pytz
from dotenv import load_dotenv

from src.config.base import getenv


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

AVAILABLE_HOURS = list(range(9, 21))
MAX_BUTTON_ROWS = 10

ADMINS = [
    5362724893,  # Pasha
    882315246,  # Sara
]

BOT_DESCRIPTION = """
This mod helps teaches to schedule their lessons.
If you had `user not found` issues, than bot just added you to the database.
"""

HELP_MESSAGE = """
If you get `user not found` error, then please use /start at least once.
Here is a list of available commands:\n
"""

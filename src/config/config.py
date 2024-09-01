from dataclasses import dataclass

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

AVAILABLE_HOURS = list(range(9, 21))
MAX_BUTTON_ROWS = 10

ADMINS = [
    5362724893,  # Pasha
    882315246,  # Sara
]

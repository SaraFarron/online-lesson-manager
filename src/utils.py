from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, time, timedelta

import aiohttp
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.orm import Session

from config import config
from config.base import getenv
from config.config import ADMINS, TIMEZONE
from database import engine
from logger import logger
from models import Reschedule, ScheduledLesson, User

MAX_HOUR = 23


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


async def notify_admins(message: str) -> None:
    """Send a message to all admins."""
    for tg_id in ADMINS.values():
        await send_message(tg_id, message)


def inline_keyboard(buttons: dict[str, str] | Iterable[tuple[str, str]]):
    """Create an inline keyboard."""
    builder = InlineKeyboardBuilder()
    if isinstance(buttons, dict):
        for callback_data, text in buttons.items():
            builder.button(text=text, callback_data=callback_data)  # type: ignore  # noqa: PGH003
    else:
        for text, callback_data in buttons:
            builder.button(text=text, callback_data=callback_data)
    return builder


def this_week():
    """Get dates for the current week."""
    today = datetime.now(TIMEZONE)
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=7)
    return [start_of_week + timedelta(n) for n in range(int((end_of_week - start_of_week).days))]


def daterange(start: datetime, end: datetime, step: int = 1) -> Iterable[datetime]:
    """Get dates between two dates."""
    date_range = []
    current_date = start

    while current_date <= end:
        date_range.append(current_date)
        current_date += timedelta(days=step)

    return date_range


def delete_banned_users():
    """Delete banned users."""
    counter = 0
    with Session(engine) as session:
        for user_id in config.BANNED_USERS:
            user = session.query(User).filter(User.telegram_id == user_id).first()
            if user is None:
                continue
            counter += 1
            session.query(Reschedule).filter(Reschedule.user_id == user.id).delete()
            session.query(ScheduledLesson).filter(ScheduledLesson.user_id == user.id).delete()
            session.query(User).filter(User.telegram_id == user_id).delete()
        session.commit()

    logger.info(f"Deleted {counter} banned users")

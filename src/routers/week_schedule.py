from __future__ import annotations

from datetime import timedelta
from datetime import datetime as dt

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy.orm import Session

from config.config import TIMEZONE
from errors import AiogramTelegramError
from help import Commands
from messages import replies
from middlewares import DatabaseMiddleware
from repositories import UserRepo
from utils import inline_keyboard
from service import Schedule

COMMAND = "week_schedule"
router: Router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())


class Callbacks:
    WEEK_START = "week_schedule_start:"


@router.message(Command(COMMAND))
@router.message(F.text == Commands.WEEK_SCHEDULE.value)
@router.callback_query(F.data.startswith(Callbacks.WEEK_START))
async def week_schedule_handler(event: Message | CallbackQuery, db: Session) -> None:
    """Handler returns today's schedule."""
    t_user = event.from_user
    if not t_user:
        raise AiogramTelegramError
    user = UserRepo(db).get_by_telegram_id(t_user.id)
    message = event if isinstance(event, Message) else event.message
    start_date = dt.now(TIMEZONE) if isinstance(event, Message) else dt.strptime(event.data.split(":")[-1], "%d-%m-%Y")
    if user:
        schedule = Schedule(db)
        start_of_week = start_date - timedelta(days=start_date.weekday())
        previous_week_start = dt.strftime(start_of_week - timedelta(days=7), "%d-%m-%Y")
        next_week_start = dt.strftime(start_of_week + timedelta(days=7), "%d-%m-%Y")
        buttons = inline_keyboard((
            ("Предыдущая неделя", Callbacks.WEEK_START + previous_week_start),
            ("Следующая неделя", Callbacks.WEEK_START + next_week_start),
        )).as_markup()
        text = schedule.lessons_week_message(user, start_of_week.date())
        await message.answer(text, reply_markup=buttons)
    else:
        await message.answer(replies.NOT_REGISTERED)

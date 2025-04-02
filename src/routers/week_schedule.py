from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy.orm import Session
from service import Service, Keyboards
from help import Commands
from middlewares import DatabaseMiddleware

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
    message = event if isinstance(event, Message) else event.message
    service = Service(db)
    user = service.get_user(message.from_user.id)

    start_of_week, previous_week_start, next_week_start = get_week_params(event)
    keyboard = Keyboards.inline_keyboard((
        ("Предыдущая неделя", Callbacks.WEEK_START + previous_week_start),
        ("Следующая неделя", Callbacks.WEEK_START + next_week_start),
    ))
    text = service.lessons_week_message(user, start_of_week.date())

    await message.answer(text, reply_markup=keyboard)

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from errors import AiogramTelegramError
from help import Commands
from messages import replies
from middlewares import DatabaseMiddleware
from utils import inline_keyboard

COMMAND = "/add_sl"
MAX_HOUR = 23

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())


class Callbacks:
    CHOOSE_WEEKDAY = "add_sl_choose_weekday:"
    CHOOSE_TIME = "add_sl_choose_time:"


@router.message(Command(COMMAND))
@router.message(F.text == Commands.ADD_SCHEDULED_LESSON.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    """First handler, gives a list of available weekdays."""
    if message.from_user is None:
        raise AiogramTelegramError

    service = Service(db)
    user = service.get_user(message.from_user.id)
    weekdays = service.get_weekdays(user)
    keyboard = inline_keyboard(weekdays)

    await message.answer(replies.CHOOSE_WEEKDAY, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_WEEKDAY))
async def add_lesson_choose_weekday_handler(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Second handler, gives a list of available times."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    weekday = int(callback.data.split(":")[1])  # type: ignore  # noqa: PGH003
    await state.update_data(weekday=weekday)
    service = Service(db)
    user = service.get_user(message.from_user.id)
    available_time = service.get_available_time(user, weekday)
    keyboard = inline_keyboard(buttons(available_time))

    await message.answer(replies.CHOOSE_TIME, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_TIME))
async def add_lesson_choose_time_handler(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Last handler, saves scheduled lesson."""
    message = callback.message
    if not isinstance(message, Message):
        raise AiogramTelegramError

    time_str = callback.data.split(":")[1]  # type: ignore  # noqa: PGH003
    time = datetime.strptime(time_str, "%H.%M").time()  # noqa: DTZ007
    state_data = await state.get_data()
    service = Service(db)
    user = service.get_user(message.from_user.id)
    service.create_sl(user, state_data["weekday"], time)
    db.commit()

    await message.answer(replies.LESSON_ADDED)

from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from errors import AiogramTelegramError
from help import Commands
from messages import replies
from middlewares import DatabaseMiddleware
from aiogram.fsm.state import State, StatesGroup
from service import Service
from utils import inline_keyboard

COMMAND = "/add_ol"
MAX_HOUR = 23

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())


class Callbacks:
    CHOOSE_WEEKDAY = "add_ol_choose_day:"
    CHOOSE_TIME = "add_ol_choose_time:"


class CreateNewLesson(StatesGroup):
    new_date = State()


@router.message(Command(COMMAND))
@router.message(F.text == Commands.ADD_ONE_LESSON.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    """First handler, gives a list of available weekdays."""
    if message.from_user is None:
        raise AiogramTelegramError

    service = Service(db)
    service.get_user(message.from_user.id)

    await state.set_state(CreateNewLesson.new_date)
    await message.answer(replies.CHOOSE_ONE_DATE)


@router.message(CreateNewLesson.new_date)
async def add_lesson_choose_day_handler(message: Message, state: FSMContext, db: Session) -> None:
    """Second handler, gives a list of available times."""
    if not isinstance(message, Message):
        raise AiogramTelegramError

    now = datetime.now(tz=config.TIMEZONE)
    try:
        day = get_date(message.text if message.text else "")
    except ValueError:
        await state.set_state(CreateNewLesson.new_date)
        await message.answer(replies.WRONG_DATE)
        return
    if day < now.date():
        await state.set_state(CreateNewLesson.new_date)
        await message.answer(replies.CHOOSE_FUTURE_DATE_ONE)
        return
    await state.update_data(day=day)

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
    service.create_lesson(user, state_data["day"], time)
    db.commit()

    await message.answer(replies.LESSON_ADDED)
    await state.clear()

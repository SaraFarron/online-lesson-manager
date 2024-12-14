from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from errors import AiogramTelegramError, PermissionDeniedError
from help import Commands
from logger import log_func
from messages import replies
from middlewares import DatabaseMiddleware
from models import ScheduledLesson
from repositories import UserRepo
from service import SecondFunctions
from utils import inline_keyboard, send_message

COMMAND = "/add_sl"
MAX_HOUR = 23

router = Router()
router.message.middleware(DatabaseMiddleware())


class Callbacks:
    CHOOSE_WEEKDAY = "add_sl_choose_weekday:"
    CHOOSE_TIME = "add_sl_choose_time:"


@router.message(Command(COMMAND))
@router.message(F.text == Commands.ADD_SCHEDULED_LESSON.value)
@log_func
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    """First handler, gives a list of available weekdays."""
    if message.from_user is None:
        raise AiogramTelegramError
    if message.from_user.id in config.BANNED_USERS:
        raise PermissionDeniedError
    available_weekdays = SecondFunctions(db, message.from_user.id).available_weekdays()

    weekdays = [(config.WEEKDAY_MAP[d], Callbacks.CHOOSE_WEEKDAY + str(d)) for d in available_weekdays]
    keyboard = inline_keyboard(weekdays)

    await state.update_data(user_id=message.from_user.id)
    await message.answer(replies.CHOOSE_WEEKDAY, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_WEEKDAY))
@log_func
async def add_lesson_choose_weekday_handler(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Second handler, gives a list of available times."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    weekday = int(callback.data.split(":")[1])  # type: ignore  # noqa: PGH003
    await state.update_data(weekday=weekday)
    state_data = await state.get_data()

    available_time = SecondFunctions(db, state_data["user_id"]).available_time_weekday(weekday)

    keyboard = inline_keyboard(
        [(t.strftime("%H:%M"), Callbacks.CHOOSE_TIME + t.strftime("%H.%M")) for t in available_time],
    )
    keyboard.adjust(1, repeat=True)

    await callback.message.answer(replies.CHOOSE_TIME, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_TIME))
@log_func
async def add_lesson_choose_time_handler(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Last handler, saves scheduled lesson."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    time_str = callback.data.split(":")[1]  # type: ignore  # noqa: PGH003
    time = datetime.strptime(time_str, "%H.%M").time()  # noqa: DTZ007
    state_data = await state.get_data()
    await state.update_data(time=time)
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"])
    if not user:
        raise PermissionDeniedError
    sl = ScheduledLesson(
        user=user,
        weekday=state_data["weekday"],
        start_time=time,
        end_time=time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0),
    )
    db.add(sl)
    db.commit()
    message = replies.USER_ADDED_SL % (user.username_dog, sl.weekday_full_str, sl.st_str)
    await send_message(user.teacher.telegram_id, message)
    await callback.message.answer(replies.LESSON_ADDED)

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
from messages import replies
from middlewares import DatabaseMiddleware
from models import Lesson
from repositories import UserRepo
from service import Schedule
from aiogram.fsm.state import State, StatesGroup

from utils import inline_keyboard, send_message

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

    user = UserRepo(db).get_by_telegram_id(message.from_user.id)

    if message.from_user.id in config.BANNED_USERS or user is None:
        raise PermissionDeniedError

    await state.update_data(user_id=user.id)

    await state.set_state(CreateNewLesson.new_date)
    await message.answer(replies.CHOOSE_ONE_DATE)



@router.message(CreateNewLesson.new_date)
async def add_lesson_choose_day_handler(message: Message, state: FSMContext, db: Session) -> None:
    """Second handler, gives a list of available times."""
    if not isinstance(message, Message):
        raise AiogramTelegramError
    now = datetime.now(tz=config.TIMEZONE)
    try:
        day = datetime.strptime(message.text if message.text else "", "%d-%m-%Y").date()  # noqa: DTZ007
    except ValueError:
        await state.set_state(CreateNewLesson.new_date)
        await message.answer(replies.WRONG_DATE)
        return
    if day < now.date():
        await state.set_state(CreateNewLesson.new_date)
        await message.answer(replies.CHOOSE_FUTURE_DATE_ONE)
        return
    await state.update_data(day=day)
    state_data = await state.get_data()

    user = UserRepo(db).get(state_data["user_id"])
    if not user:
        raise PermissionDeniedError
    available_time = Schedule(db).available_time_with_reschedules(user, day)

    buttons = []
    for t, s in available_time:
        if s:
            button_text = f"{t.strftime("%H:%M")} ({s})"
        else:
            button_text = t.strftime("%H:%M")
        buttons.append((button_text, Callbacks.CHOOSE_TIME + t.strftime("%H.%M")))

    keyboard = inline_keyboard(buttons)
    keyboard.adjust(1, repeat=True)
    await message.answer(replies.CHOOSE_TIME, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_TIME))
async def add_lesson_choose_time_handler(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Last handler, saves scheduled lesson."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    time_str = callback.data.split(":")[1]  # type: ignore  # noqa: PGH003
    time = datetime.strptime(time_str, "%H.%M").time()  # noqa: DTZ007
    state_data = await state.get_data()
    await state.update_data(time=time)
    user = UserRepo(db).get(state_data["user_id"])
    if not user:
        raise PermissionDeniedError
    lesson = Lesson(
        user=user,
        date=state_data["day"],
        start_time=time,
        end_time=time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0),
    )
    db.add(lesson)
    db.commit()
    message = replies.USER_ADDED_SL % (user.username_dog, lesson.date, lesson.st_str)
    await send_message(user.teacher.telegram_id, message)
    await callback.message.answer(replies.LESSON_ADDED)

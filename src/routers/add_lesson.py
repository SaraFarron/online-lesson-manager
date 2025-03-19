from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils import keyboard
from sqlalchemy.orm import Session

from callbacks import AddLessonCallback
from states import AddLessonState
from errors import AiogramTelegramError
from help import Commands
from messages import replies
from middlewares import DatabaseMiddleware
from service import Service, Keyboards
from utils import telegram_checks, parse_date, get_callback_arg

COMMAND = "/add_sl"

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())


@router.message(Command(COMMAND))
@router.message(F.text == Commands.ADD_SCHEDULED_LESSON.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)

    service = Service(db)
    user = service.get_user(message.from_user.id)
    keyboard = Keyboards.choose_lesson_type(AddLessonCallback.choose_weekday, AddLessonCallback.choose_day)

    await message.answer(replies.CHOOSE_LESSON_TYPE, reply_markup=keyboard)


@router.callback_query(F.data.startswith(AddLessonCallback.choose_weekday))
async def choose_weekday(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)

    service = Service(db)
    user = service.get_user(message.from_user.id)
    weekdays = service.available_weekdays(user)
    keyboard = Keyboards.weekdays(weekdays, AddLessonCallback.choose_time)

    await message.answer(replies.CHOOSE_WEEKDAY, reply_markup=keyboard)


@router.callback_query(F.data.startswith(AddLessonCallback.choose_time))
async def choose_day(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)

    service = Service(db)
    user = service.get_user(message.from_user.id)
    await state.set_state(AddLessonState.choose_day)
    await message.answer(replies.CHOOSE_ONE_DATE)



@router.callback_query(F.data.startswith(AddLessonCallback.choose_time))
@router.message(AddLessonState.choose_day)
async def choose_time(event: CallbackQuery | Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(event)

    service = Service(db)
    user = service.get_user(message.from_user.id)

    if isinstance(event, Message):
        day = parse_date(message.text)
        if day is None:
            await state.set_state(AddLessonState.choose_day)
            await message.answer(replies.WRONG_DATE)
        await state.update_data(day=day)
        available_time = service.available_time(user, day)
    else:
        weekday = get_callback_arg(event.data, AddLessonCallback.choose_time)
        await state.update_data(weekday=weekday)
        available_time = service.available_time(user, weekday)
    keyboard = Keyboards.choose_time(available_time, AddLessonCallback.finish)

    await state.clear()
    await message.answer(replies.CHOOSE_TIME, reply_markup=keyboard)










@router.callback_query(F.data.startswith(AddLessonCallback.CHOOSE_WEEKDAY))
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


@router.callback_query(F.data.startswith(AddLessonCallback.choose_time))
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

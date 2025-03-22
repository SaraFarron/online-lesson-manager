from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from callbacks import AddLessonCallback
from help import Commands
from messages import replies
from middlewares import DatabaseMiddleware
from service import Service, Keyboards
from states import AddLessonState
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


@router.callback_query(F.data.startswith(AddLessonCallback.finish))
async def finish(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)

    service = Service(db)
    user = service.get_user(message.from_user.id)

    time = get_callback_arg(callback.data, AddLessonCallback.finish)
    state_data = await state.get_data()

    if "day" in state_data:
        service.create_lesson(user, state_data["day"], time)
    elif "weekday" in state_data:
        service.create_weekly_lesson(user, state_data["weekday"], time)
    else:
        raise ValueError(f"Unknown state data: {state_data}")

    await message.answer(replies.LESSON_ADDED)

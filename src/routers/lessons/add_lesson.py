from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.core import config
from src.core.help import Commands
from src.keyboards import Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.models import Event
from src.repositories import EventHistoryRepo, EventRepo, UserRepo
from src.utils import get_callback_arg, parse_date, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class AddLesson(StatesGroup):
    scene = "add_lesson"
    command = "/" + scene
    base_callback = scene + "/"
    choose_date = State()
    choose_time = f"{base_callback}choose_time/"
    finish = f"{base_callback}finish/"


@router.message(Command(AddLesson.command))
@router.message(F.text == Commands.ADD_LESSON.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)

    await state.update_data(user_id=user.telegram_id)
    await message.answer(replies.CHOOSE_LESSON_DATE)
    await state.set_state(AddLesson.choose_date)


@router.message(AddLesson.choose_date)
async def choose_date(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)

    date = parse_date(message.text)
    if date is None:
        await state.set_state(AddLesson.choose_date)
        await message.answer(replies.WRONG_DATE_FMT)
        return
    await state.update_data(day=date)

    available_time = EventRepo(db).available_time(user.executor_id, date)
    available_time = [s for s, e in available_time]
    await message.answer(
        replies.CHOOSE_TIME, reply_markup=Keyboards.choose_time(available_time, AddLesson.choose_time)
    )


@router.callback_query(F.data.startswith(AddLesson.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    date = state_data["day"]
    time = datetime.strptime(
        get_callback_arg(callback.data, AddLesson.choose_time),
        config.TIME_FMT
    ).time()

    lesson = Event(
        user_id=user.id,
        executor_id=user.executor_id,
        event_type=Event.EventTypes.LESSON,
        start=datetime.combine(date, time),
        end=datetime.combine(date, time.replace(hour=time.hour + 1)),
    )
    db.add(lesson)
    db.commit()
    await message.answer(replies.LESSON_ADDED)
    EventHistoryRepo(db).create(user.username, AddLesson.scene, "added_lesson", str(lesson))
    await state.clear()

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.core.config import LESSON_SIZE
from src.keyboards import Commands, Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.models import RecurrentEvent
from src.repositories import EventHistoryRepo, EventRepo, UserRepo
from src.utils import get_callback_arg, send_message, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class AddRecurrentLesson(StatesGroup):
    scene = "add_recurrent_lesson"
    command = "/" + scene
    base_callback = scene + "/"
    choose_weekday = f"{base_callback}choose_weekday/"
    choose_time = f"{base_callback}choose_time/"


@router.message(Command(AddRecurrentLesson.command))
@router.message(F.text == Commands.ADD_RECURRENT_LESSON.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)

    await state.update_data(user_id=user.telegram_id)
    weekdays = EventRepo(db).available_weekdays(user.executor_id)
    await message.answer(replies.CHOOSE_WEEKDAY, reply_markup=Keyboards.weekdays(weekdays, AddRecurrentLesson.choose_weekday))


@router.callback_query(F.data.startswith(AddRecurrentLesson.choose_weekday))
async def choose_weekday(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    weekday = int(get_callback_arg(callback.data, AddRecurrentLesson.choose_weekday))
    available_time = EventRepo(db).available_time_weekday(user.executor_id, weekday)
    await state.update_data(weekday=weekday)
    await message.answer(replies.CHOOSE_TIME, reply_markup=Keyboards.choose_time(available_time, AddRecurrentLesson.choose_time))


@router.callback_query(F.data.startswith(AddRecurrentLesson.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    time = datetime.strptime(get_callback_arg(callback.data, AddRecurrentLesson.choose_time), "%H:%M").time()
    start_of_week = datetime.now().date() - timedelta(days=datetime.now().weekday())
    current_day = start_of_week + timedelta(days=state_data["weekday"])
    start = datetime.combine(current_day, time)
    lesson = RecurrentEvent(
        user_id=user.id,
        executor_id=user.executor_id,
        event_type=RecurrentEvent.EventTypes.LESSON,
        start=start,
        end=start + LESSON_SIZE,
        interval=7,
    )
    db.add(lesson)
    db.commit()
    username = user.username if user.username else user.full_name
    await message.answer(replies.LESSON_ADDED)
    EventHistoryRepo(db).create(username, AddRecurrentLesson.scene, "added_lesson", str(lesson))
    executor_tg = UserRepo(db).executor_telegram_id(user)
    await send_message(executor_tg, f"{username} добавил(а) {lesson}")
    await state.clear()

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import CallbackQuery, Message
from db.repositories import EventHistoryRepo, UserRepo
from sqlalchemy.orm import Session

from service.lessons import LessonsService
from service.services import EventService, UserService
from service.utils import get_callback_arg, send_message
from src.core.middlewares import DatabaseMiddleware
from src.interface.keyboards import Commands, Keyboards
from src.interface.messages import replies
from src.interface.utils import auto_place_work_breaks

router = Router()


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
    message, user = UserService(db).check_user(message)

    await state.update_data(user_id=user.telegram_id)
    weekdays = EventService(db).available_weekdays(user.executor_id)
    await message.answer(
        replies.CHOOSE_WEEKDAY,
        reply_markup=Keyboards.weekdays(weekdays, AddRecurrentLesson.choose_weekday),
    )


@router.callback_query(F.data.startswith(AddRecurrentLesson.choose_weekday))
async def choose_weekday(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"])

    weekday = int(get_callback_arg(callback.data, AddRecurrentLesson.choose_weekday))
    available_time, _ = EventService(db).available_time_weekday(user.executor_id, weekday)
    if not available_time:
        await message.answer(replies.NO_TIME)
        await state.clear()
        return
    await state.update_data(weekday=weekday)
    await message.answer(
        replies.CHOOSE_TIME,
        reply_markup=Keyboards.choose_time(available_time, AddRecurrentLesson.choose_time),
    )


@router.callback_query(F.data.startswith(AddRecurrentLesson.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"])

    time = datetime.strptime(get_callback_arg(callback.data, AddRecurrentLesson.choose_time), "%H:%M").time()
    lesson = LessonsService(db).create_recurrent_lesson(
        user_id=user.id,
        executor_id=user.executor_id,
        weekday=state_data["weekday"],
        time=time,
    )
    await message.answer(replies.LESSON_ADDED)
    EventHistoryRepo(db).create(user.get_username(), AddRecurrentLesson.scene, "added_lesson", str(lesson))
    executor_tg = UserRepo(db).executor_telegram_id(user)
    await send_message(executor_tg, f"{user.get_username()} добавил(а) {lesson}")
    now = datetime.now()
    start_of_week = now.date() - timedelta(days=now.weekday())
    current_day = start_of_week + timedelta(days=state_data["weekday"])
    await auto_place_work_breaks(db, user, current_day, executor_tg)
    await state.clear()

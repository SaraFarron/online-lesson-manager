from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from service.utils import get_callback_arg, parse_date, send_message
from src.core.config import DATE_FMT
from src.keyboards import Commands, Keyboards
from src.messages import replies
from src.service import UserService
from src.service.utils import telegram_checks
from src.states import AddLesson
from src.utils import auto_place_work_breaks

router = Router()


@router.message(Command(AddLesson.command))
@router.message(F.text == Commands.ADD_LESSON.value)
async def add_lesson_handler(message: Message, state: FSMContext) -> None:
    message = telegram_checks(message)
    service = UserService(message)
    user = await service.get_user()
    if user is None:
        await message.answer(replies.PERMISSION_DENIED)
        return

    await state.update_data(user_id=user.telegram_id)
    await message.answer(replies.CHOOSE_LESSON_DATE)
    await state.set_state(AddLesson.choose_date)


@router.message(AddLesson.choose_date)
async def choose_date(message: Message, state: FSMContext, db: Session) -> None:
    message, user = UserService(db).check_user_with_id(message, message.from_user.id)

    day = parse_date(message.text)
    today = datetime.now().date()
    if day is None:
        await state.set_state(AddLesson.choose_date)
        await message.answer(replies.WRONG_DATE_FMT)
        return
    if today > day:
        await state.set_state(AddLesson.choose_date)
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        if len(message.text) <= 7:
            await message.answer(replies.ADD_YEAR)
        return

    await state.update_data(day=day)

    available_time, _ = EventService(db).available_time(user.executor_id, day)
    if available_time:
        await message.answer(
            replies.CHOOSE_TIME,
            reply_markup=Keyboards.choose_time(available_time, AddLesson.choose_time),
        )
    else:
        await message.answer(replies.NO_TIME)
        await state.clear()


@router.callback_query(F.data.startswith(AddLesson.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"])
    date = datetime.strptime(state_data["day"], DATE_FMT) if isinstance(state_data["day"], str) else state_data["day"]

    lesson = LessonsService(db).create_lesson(
        user_id=user.id,
        executor_id=user.executor_id,
        date=date,
        time=get_callback_arg(callback.data, AddLesson.choose_time),
    )

    await message.answer(replies.LESSON_ADDED)
    EventHistoryRepo(db).create(user.get_username(), AddLesson.scene, "added_lesson", str(lesson))
    executor_tg = UserRepo(db).executor_telegram_id(user)
    await send_message(executor_tg, f"{user.get_username()} добавил(а) {lesson}")
    await auto_place_work_breaks(db, user, date, executor_tg)
    await state.clear()

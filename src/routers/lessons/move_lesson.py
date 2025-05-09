from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.core.help import Commands
from datetime import timedelta, datetime
from src.keyboards import Keyboards
from src.middlewares import DatabaseMiddleware
from src.models import RecurrentEvent
from src.repositories import EventHistoryRepo, EventRepo, UserRepo
from src.utils import get_callback_arg, parse_date, telegram_checks
from src.core import config
from src.messages import replies

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class MoveLesson(StatesGroup):
    scene = "move_lesson"
    command = "/" + scene
    base_callback = scene + "/"
    choose_lesson = f"{base_callback}choose_lesson/"


@router.message(Command(MoveLesson.command))
@router.message(F.text == Commands.MOVE_LESSON.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)

    await state.update_data(user_id=user.telegram_id)
    lessons = EventRepo(db).all_user_lessons(user)
    if lessons:
        await message.answer(replies.CHOOSE_LESSON, reply_markup=Keyboards.choose_lesson(lessons, MoveLesson.choose_lesson))
    else:
        await message.answer(replies.NO_LESSONS)


@router.callback_query(F.data.startswith(MoveLesson.choose_lesson))
async def choose_weekday(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

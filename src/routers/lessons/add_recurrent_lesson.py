from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.callbacks import AddRecurrentLessonCallback
from src.core.help import Commands
from datetime import timedelta, datetime
from src.keyboards import Keyboards
from src.middlewares import DatabaseMiddleware
from src.models import Event
from src.repositories import EventHistoryRepo, EventRepo, UserRepo
from src.utils import get_callback_arg, parse_date, telegram_checks
from src.core import config
from src.messages import replies

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class AddRecurrentLesson(StatesGroup):
    scene = "add_recurrent_lesson"
    command = "/" + scene


@router.message(Command(AddRecurrentLesson.command))
@router.message(F.text == Commands.ADD_RECURRENT_LESSON.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)
    await state.update_data(user_id=user.telegram_id)
    weekdays = EventRepo(db).available_weekdays(user.id)
    await message.answer(replies.CHOOSE_WEEKDAY, reply_markup=Keyboards.weekdays(weekdays, AddRecurrentLessonCallback.choose_weekday))




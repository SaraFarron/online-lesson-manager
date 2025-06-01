from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import Message
from sqlalchemy.orm import Session

from src.keyboards import Commands
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.models import User
from src.repositories import EventRepo, UserRepo
from src.utils import day_schedule_text, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class DaySchedule(StatesGroup):
    scene = "day_schedule"
    command = "/" + scene
    base_callback = scene + "/"


@router.message(Command(DaySchedule.command))
@router.message(F.text == Commands.DAY_SCHEDULE.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)

    lessons = EventRepo(db).day_schedule(
        user.executor_id,
        datetime.now().date(),
        None if user.role == User.Roles.TEACHER else user.id,
    )
    users_map = {
        u.id: u.username if u.username else u.full_name for u in db.query(User).filter(User.executor_id == user.executor_id)
    }
    result = day_schedule_text(lessons, users_map, user)
    await message.answer("\n\n".join(result) if result else replies.NO_LESSONS)
    await state.clear()

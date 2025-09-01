from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import Message
from sqlalchemy.orm import Session

from src.db.models import User
from src.keyboards import Commands
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.services import EventService, UserService
from src.utils import day_schedule_text

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
    message, user = UserService(db).check_user(message)

    lessons = EventService(db).day_schedule(
        user.executor_id,
        datetime.now().date(),
        None if user.role == User.Roles.TEACHER else user.id,
    )
    users_map = {
        u.id: f"@{u.username}" if u.username else f'<a href="tg://user?id={u.telegram_id}">{u.full_name}</a>'
        for u in db.query(User).filter(User.executor_id == user.executor_id)
    }
    result = day_schedule_text(lessons, users_map, user)
    await message.answer("\n".join(result) if result else replies.NO_LESSONS)
    await state.clear()

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import Message
from sqlalchemy.orm import Session

from src.core.config import DATE_FMT, TIME_FMT, WEEKDAY_MAP
from src.core.help import Commands
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.models import User, Event, RecurrentEvent
from src.repositories import EventRepo, UserRepo
from src.utils import telegram_checks

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
    users_map = {u.id: u.username for u in db.query(User).filter(User.executor_id == user.executor_id)}
    result = []
    for lesson in lessons:
        if lesson[3] in (Event.EventTypes.LESSON, Event.EventTypes.MOVED_LESSON):
            dt = lesson[0]
            lesson_str = f"{lesson[3]} {datetime.strftime(dt, DATE_FMT)} в {datetime.strftime(dt, TIME_FMT)}"
        elif lesson[3] == RecurrentEvent.EventTypes.LESSON:
            dt = lesson[0]
            weekday = WEEKDAY_MAP[dt.weekday()]["short"]
            lesson_str = f"{lesson[3]} {weekday} в {datetime.strftime(dt, TIME_FMT)}"
        else:
            continue
        if user.role == User.Roles.TEACHER:
            lesson_str += f"у {users_map[lesson[2]]}"
        result.append(lesson_str)
    await message.answer("\n\n".join(result) if result else replies.NO_LESSONS)
    await state.clear()

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.core.config import DATE_FMT
from src.keyboards import Commands, Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.models import User
from src.repositories import EventRepo, UserRepo
from src.utils import day_schedule_text, get_callback_arg, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class WeekSchedule(StatesGroup):
    scene = "week_schedule"
    command = "/" + scene
    base_callback = scene + "/"
    week_start = f"{base_callback}week_start/"


@router.message(Command(WeekSchedule.command))
@router.message(F.text == Commands.WEEK_SCHEDULE.value)
@router.callback_query(F.data.startswith(WeekSchedule.week_start))
async def week_schedule_handler(event: Message | CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(event)
    state_data = await state.get_data()
    if isinstance(event, CallbackQuery):
        user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)
    else:
        user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)
        await state.update_data(user_id=message.from_user.id)

    users_map = {
        u.id: u.username if u.username else u.full_name for u in db.query(User).filter(User.executor_id == user.executor_id)
    }
    if isinstance(event, Message):
        date = datetime.now()
    else:
        date = datetime.strptime(get_callback_arg(event.data, WeekSchedule.week_start), DATE_FMT)
    start_of_week = date - timedelta(days=date.weekday())
    date_lesson_map = {}
    for i in range(7):
        current_date = start_of_week + timedelta(days=i)
        lessons = EventRepo(db).day_schedule(
            user.executor_id,
            current_date.date(),
            None if user.role == User.Roles.TEACHER else user.id,
        )
        result = day_schedule_text(lessons, users_map, user)
        lessons_str = "\n".join(result) if result else replies.NO_LESSONS
        date_lesson_map[datetime.strftime(current_date, DATE_FMT)] = lessons_str

    text = []
    for d, day_text in date_lesson_map.items():
        # TODO: add style
        text.append(d + "\n" + day_text)
    await message.answer("\n\n".join(text), reply_markup=Keyboards.choose_week(date, WeekSchedule.week_start))


from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import Message
from sqlalchemy.orm import Session

from service.schedule import ScheduleService
from service.services import UserService
from src.keyboards import Commands

router = Router()


router.callback_query.middleware(DatabaseMiddleware())

class DaySchedule(StatesGroup):
    scene = "day_schedule"
    command = "/" + scene
    base_callback = scene + "/"


@router.message(Command(DaySchedule.command))
@router.message(F.text == Commands.DAY_SCHEDULE.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    message, user = UserService(db).check_user(message)

    day_schedule = ScheduleService(db).day_schedule_prompt(user)

    await message.answer(day_schedule)
    await state.clear()

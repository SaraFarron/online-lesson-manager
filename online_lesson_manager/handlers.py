from datetime import datetime

from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from sqlalchemy.orm import Session

from online_lesson_manager.database import engine
from online_lesson_manager.keyborads import available_time, calendar
from online_lesson_manager.models import Lesson, User
from online_lesson_manager.states import AddLesson

router: Router = Router()


@router.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    """Handler receives messages with `/start` command."""
    await message.answer(f"你好, {html.bold(message.from_user.full_name)}!")


@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `/cancel` command."""
    current_state = await state.get_state()
    if current_state is None:
        return

    await state.clear()
    await message.answer("Canceled", reply_markup=ReplyKeyboardRemove())


@router.message(Command("get_schedule"))
async def get_schedule(message: Message) -> None:
    """Handler receives messages with `/schedule` command."""
    await message.answer("Calendar", reply_markup=calendar())


@router.message(Command("add_lesson"))
async def add_lesson(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `/add_lesson` command."""
    await state.set_state(AddLesson.choose_date)
    await message.answer("Choose date", reply_markup=calendar())


@router.message(AddLesson.choose_date)
async def choose_date(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `choose_date` state."""
    day = datetime.strptime(message.text, "%d.%m").date()
    await state.update_data(date=day)
    await state.set_state(AddLesson.choose_time)
    await message.answer("Choose time", reply_markup=available_time(day))


@router.message(AddLesson.choose_time)
async def choose_time(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `choose_time` state."""
    time = datetime.strptime(message.text, "%H:%M").time()
    await state.update_data(time=time)
    with Session(engine) as session:
        user = session.get(User, message.from_user.full_name)
        if not user:
            await message.answer("User not found")
            return
        lesson = Lesson(
            date=state.data["date"],
            time=state.data["time"],
            user_id=user.id,
        )
        session.add(lesson)
        session.commit()
    await message.answer("Lesson added")

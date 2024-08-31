from datetime import datetime

from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.orm import Session

from src.callbacks import DateCallBack, TimeCallBack
from src.config.config import DATE_FORMAT, TIME_FORMAT
from src.database import engine
from src.keyborads import available_time, calendar
from src.logger import logger
from src.models import Lesson, User
from src.states import AddLesson

router: Router = Router()


@router.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    """Handler receives messages with `/start` command."""
    with Session(engine) as session:
        if not session.query(User).filter(User.telegram_id == message.from_user.id).first():
            user = User(name=message.from_user.full_name, telegram_id=message.from_user.id)
            session.add(user)
            session.commit()
            logger.info("User %s registered", message.from_user.full_name)
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
    logger.info("User %s wants to add a lesson", message.from_user.full_name)
    await state.set_state(AddLesson.choose_date)
    await state.update_data(user_id=message.from_user.id, name=message.from_user.full_name)
    await message.answer("Choose date", reply_markup=calendar())


@router.callback_query(DateCallBack.filter())
async def choose_date(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_date` state."""
    logger.info("User %s chose %s date", callback.from_user.full_name, callback.data)
    day = datetime.strptime(callback.data, f"choose_date:{DATE_FORMAT}").date()
    await state.update_data(date=day)
    await state.set_state(AddLesson.choose_time)
    await callback.message.answer("Choose time", reply_markup=available_time(day))


@router.callback_query(TimeCallBack.filter())
async def choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_time` state."""
    state_data = await state.get_data()
    logger.info("User %s chose %s time", callback.from_user.full_name, callback.data)
    time = datetime.strptime(callback.data, f"choose_time:{TIME_FORMAT}").time()  # Separator symbol ':' can not be used
    await state.update_data(time=time)
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == state_data["user_id"]).first()
        if not user:
            await callback.message.answer(f"User {state_data["name"]} not found")
            logger.info("User %s:%s not found", state_data["user_id"], state_data["name"])
            return
        lesson = Lesson(
            date=state_data["date"],
            time=time,
            user_id=user.id,
            status="upcoming",
            end_time=time.replace(hour=time.hour + 1) if time.hour < 23 else time.replace(hour=0, minute=time.minute),
        )
        session.add(lesson)
        session.commit()
    await callback.message.answer("Lesson added")

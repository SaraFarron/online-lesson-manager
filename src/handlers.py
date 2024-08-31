from datetime import datetime

from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.orm import Session

from src.callbacks import DateCallBack, RemoveLessonCallBack, TimeCallBack, YesNoCallBack
from src.config.config import BOT_DESCRIPTION, DATE_FORMAT, HELP_MESSAGE, TIME_FORMAT, TIMEZONE
from src.database import engine
from src.keyborads import available_commands, available_time, calendar, lessons_to_remove, yes_no
from src.logger import logger
from src.models import Lesson, User
from src.states import AddLesson
from src.utils import get_todays_schedule, notify_admins

router: Router = Router()


@router.message(Command("help"))
async def get_help(message: Message) -> None:
    """Handler receives messages with `/help` command."""
    await message.answer(HELP_MESSAGE, reply_markup=available_commands(message.from_user.id))


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
    await message.answer(BOT_DESCRIPTION)


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
    today = datetime.now(TIMEZONE)
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            logger.info("User %s requested schedule", message.from_user.full_name)
            await message.answer(get_todays_schedule(today, user.id, user.telegram_id))
        else:
            logger.info("User %s requested schedule without registration", message.from_user.full_name)
            await message.answer("You are not registered. Please use /start command")


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
        username = user.name
        lesson_date, lesson_time = lesson.date, lesson.time
    await callback.message.answer("Lesson added")
    await notify_admins(f"{username} added a lesson {lesson_date} at {lesson_time}")
    await state.clear()


@router.message(Command("remove_lesson"))
async def remove_lesson(message: Message) -> None:
    """Handler receives messages with `/remove_lesson` command."""
    logger.info("User %s wants to remove a lesson", message.from_user.full_name)
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            lessons = session.query(Lesson).filter(Lesson.user_id == user.id, Lesson.status == "upcoming").all()
            if lessons:
                await message.answer("Choose lesson to remove", reply_markup=lessons_to_remove(lessons))
            else:
                await message.answer("No upcoming lessons")
        else:
            await message.answer("You are not registered. Please use /start command")


@router.callback_query(RemoveLessonCallBack.filter())
async def choose_lesson_to_remove(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_lesson_to_remove` state."""
    logger.info("User %s chose %s lesson to remove", callback.from_user.full_name, callback.data)
    with Session(engine) as session:
        # logger.info("CALLBACK DATA: %s", callback.data)
        lesson_id = int(callback.data.replace("remove_lesson:", ""))
        lesson = session.query(Lesson).filter(Lesson.id == lesson_id).first()
        await state.update_data(user_id=lesson.user.telegram_id, name=lesson.user.name)
        if lesson:
            lesson.status = "canceled"
            session.commit()
            await notify_admins(f"{lesson.user.name} canceled lesson at {lesson.date} {lesson.time}")
        else:
            await callback.message.answer("Lesson not found")
            return
    await callback.message.answer("Would you like to set a new date?", reply_markup=yes_no())


@router.callback_query(YesNoCallBack.filter())
async def set_new_date(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `set_new_date` state."""
    logger.info("User %s chose %s", callback.from_user.full_name, callback.data)
    if callback.data == "yes_no:yes":
        # state_data = await state.get_data()
        await state.set_state(AddLesson.choose_date)
        # await state.update_data(user_id=state_data["user_id"], name=state_data["user_name"])
        await callback.message.answer("Choose new date", reply_markup=calendar())
    else:
        await callback.message.answer("Lesson canceled")

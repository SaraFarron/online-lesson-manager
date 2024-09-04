from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import help, logs, messages, notifications
from config.config import DATE_FORMAT, TIME_FORMAT, TIMEZONE
from database import engine
from lessons.callbacks import (
    DateCallBack,
    RemoveLessonCallBack,
    TimeCallBack,
    WeekdayCallback,
    YesNoCallBack,
)
from lessons.keyboards import (
    available_schedule_keyboard,
    available_time_keyboard,
    calendar_keyboard,
    lessons_to_remove_keyboard,
    weekdays_keyboard,
    yes_no_keyboard,
)
from lessons.states import AddLesson
from lessons.utils import get_todays_schedule, get_weeks_schedule
from logger import log_func, logger
from models import Lesson, User
from utils import notify_admins

router: Router = Router()


@router.message(Command("get_schedule"))
@router.message(F.text == help.GET_SCHEDULE)
@log_func
async def get_schedule(message: Message) -> None:
    """Handler receives messages with `/get_schedule` command."""
    today = datetime.now(TIMEZONE)
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            logger.info(logs.REQUEST_SCHEDULE, message.from_user.full_name)
            await message.answer(get_todays_schedule(today, user.id, user.telegram_id))
        else:
            logger.warn(logs.REQUEST_SCHEDULE_NO_USER, message.from_user.full_name)
            await message.answer(messages.NOT_REGISTERED)


@router.message(Command("get_schedule_week"))
@router.message(F.text == help.GET_SCHEDULE_WEEK)
@log_func
async def get_schedule_week(message: Message) -> None:
    """Handler receives messages with `/get_schedule_week` command."""
    today = datetime.now(TIMEZONE)
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            logger.info(logs.REQUEST_SCHEDULE, message.from_user.full_name)
            await message.answer(get_weeks_schedule(today, user.id, user.telegram_id))
        else:
            logger.warn(logs.REQUEST_SCHEDULE_NO_USER, message.from_user.full_name)
            await message.answer(messages.NOT_REGISTERED)


@router.message(Command("add_scheduled_lesson"))
@router.message(F.text == help.ADD_SCHEDULED_LESSON)
@log_func
async def add_scheduled_lesson(message: Message) -> None:
    """Handler receives messages with `/add_scheduled_lesson` command."""
    await message.answer(messages.CHOOSE_DATE, reply_markup=weekdays_keyboard())


@router.callback_query(WeekdayCallback.filter())
@log_func
async def choose_scheduled_time(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_scheduled_time` state."""
    await state.update_data(weekday=callback.data)
    await state.set_state(AddLesson.choose_date)
    await callback.answer(messages.CHOOSE_DATE, reply_markup=available_schedule_keyboard(callback.data))


@router.message(Command("add_lesson"))
@router.message(F.text == help.ADD_LESSON)
@log_func
async def add_lesson(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `/add_lesson` command."""
    await state.set_state(AddLesson.choose_date)
    await state.update_data(user_id=message.from_user.id, name=message.from_user.full_name)
    await message.answer(messages.CHOOSE_DATE, reply_markup=calendar_keyboard())


@router.callback_query(DateCallBack.filter())
@log_func
async def choose_date(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_date` state."""
    day = datetime.strptime(callback.data, f"choose_date:{DATE_FORMAT}").date()  # noqa: DTZ007
    await state.update_data(date=day)
    await state.set_state(AddLesson.choose_time)
    await callback.message.answer(messages.CHOOSE_TIME, reply_markup=available_time_keyboard(day))


@router.callback_query(TimeCallBack.filter())
@log_func
async def choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_time` state."""
    state_data = await state.get_data()
    # Separator symbol ':' can not be used in callback_data
    time = datetime.strptime(callback.data, f"choose_time:{TIME_FORMAT}").time()  # noqa: DTZ007
    await state.update_data(time=time)
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == state_data["user_id"]).first()
        if not user:
            await callback.message.answer(messages.USER_NOT_FOUND % state_data["name"])
            logger.info(logs.USER_NOT_FOUND, state_data["user_id"], state_data["name"])
            return
        end_time = time.replace(hour=time.hour + 1) if time.hour < 23 else time.replace(hour=0, minute=time.minute)
        lesson = Lesson(
            date=state_data["date"],
            time=time,
            user_id=user.id,
            status="upcoming",
            end_time=end_time,
        )
        session.add(lesson)
        session.commit()
        username = user.name
        lesson_date, lesson_time = lesson.date, lesson.time
    await callback.message.answer(messages.LESSON_ADDED)
    await notify_admins(notifications.ADD_LESSON % (username, lesson_date, lesson_time))
    await state.clear()


@router.message(Command("remove_lesson"))
@router.message(F.text == help.REMOVE_LESSON)
@log_func
async def remove_lesson(message: Message) -> None:
    """Handler receives messages with `/remove_lesson` command."""
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            lessons = session.query(Lesson).filter(Lesson.user_id == user.id, Lesson.status == "upcoming").all()
            if lessons:
                await message.answer(messages.CHOOSE_LESSON, reply_markup=lessons_to_remove_keyboard(lessons))
            else:
                await message.answer(messages.NO_LESSONS)
        else:
            await message.answer(messages.NOT_REGISTERED)


@router.callback_query(RemoveLessonCallBack.filter())
@log_func
async def choose_lesson_to_remove(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_lesson_to_remove` state."""
    with Session(engine) as session:
        lesson_id = int(callback.data.replace("remove_lesson:", ""))
        lesson = session.query(Lesson).filter(Lesson.id == lesson_id).first()
        await state.update_data(user_id=lesson.user.telegram_id, name=lesson.user.name)
        if lesson:
            lesson.status = "canceled"
            session.commit()
            await notify_admins(notifications.CANCEL_LESSON % (lesson.user.name, lesson.date, lesson.time))
        else:
            await callback.message.answer(messages.NO_LESSON)
            return
    await callback.message.answer(messages.NEW_DATE, reply_markup=yes_no_keyboard())


@router.callback_query(YesNoCallBack.filter())
@log_func
async def set_new_date(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `set_new_date` state."""
    if callback.data == "yes_no:yes":
        await state.set_state(AddLesson.choose_date)
        await callback.message.answer(messages.CHOOSE_NEW_DATE, reply_markup=calendar_keyboard())
    else:
        await callback.message.answer(messages.LESSON_CANCELED)

import json
from datetime import datetime

import aiofiles
from aiogram import F, Router, html
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from sqlalchemy.orm import Session

from callbacks import (
    DateCallBack,
    EditWeekdayCallBack,
    RemoveLessonCallBack,
    TimeCallBack,
    WeekdayCallBack,
    YesNoCallBack,
)
from config import help, logs, messages, notifications
from config.config import ADMINS, DATE_FORMAT, TIME_FORMAT, TIMEZONE, WORK_SCHEDULE_TIMETABLE_PATH
from config.messages import BOT_DESCRIPTION, HELP_MESSAGE
from database import engine
from keyborads import (
    available_commands,
    available_time,
    calendar,
    lessons_to_remove,
    working_hours_keyboard,
    working_hours_on_day_keyboard,
    yes_no,
)
from logger import logger
from models import Lesson, User
from states import AddLesson, NewTime
from utils import (
    get_todays_schedule,
    get_weeks_schedule,
    notify_admins,
)

router: Router = Router()


@router.message(Command("help"))
@router.message(F.text == help.HELP)
async def get_help(message: Message) -> None:
    """Handler receives messages with `/help` command."""
    await message.answer(HELP_MESSAGE, reply_markup=available_commands(message.from_user.id))


@router.message(CommandStart())
@router.message(F.text == help.START)
async def command_start_handler(message: Message) -> None:
    """Handler receives messages with `/start` command."""
    with Session(engine) as session:
        if not session.query(User).filter(User.telegram_id == message.from_user.id).first():
            user = User(name=message.from_user.full_name, telegram_id=message.from_user.id)
            session.add(user)
            session.commit()
            logger.info(logs.USER_REGISTERED, message.from_user.full_name)
    await message.answer(messages.GREETINGS % html.bold(message.from_user.full_name))
    await message.answer(BOT_DESCRIPTION)


@router.message(Command("cancel"))
@router.message(F.text == help.CANCEL)
async def cancel_handler(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `/cancel` command."""
    current_state = await state.get_state()
    if current_state is None:
        return

    await state.clear()
    await message.answer(messages.CANCELED, reply_markup=ReplyKeyboardRemove())


@router.message(Command("get_schedule"))
@router.message(F.text == help.GET_SCHEDULE)
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


@router.message(Command("add_lesson"))
@router.message(F.text == help.ADD_LESSON)
async def add_lesson(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `/add_lesson` command."""
    logger.info(logs.ADD_LESSON_INTENT, message.from_user.full_name)
    await state.set_state(AddLesson.choose_date)
    await state.update_data(user_id=message.from_user.id, name=message.from_user.full_name)
    await message.answer(messages.CHOOSE_DATE, reply_markup=calendar())


@router.callback_query(DateCallBack.filter())
async def choose_date(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_date` state."""
    logger.info(logs.ADD_LESSON_DATE, callback.from_user.full_name, callback.data)
    day = datetime.strptime(callback.data, f"choose_date:{DATE_FORMAT}").date()  # noqa: DTZ007
    await state.update_data(date=day)
    await state.set_state(AddLesson.choose_time)
    await callback.message.answer(messages.CHOOSE_TIME, reply_markup=available_time(day))


@router.callback_query(TimeCallBack.filter())
async def choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_time` state."""
    state_data = await state.get_data()
    logger.info(logs.ADD_LESSON_TIME, callback.from_user.full_name, callback.data)
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
async def remove_lesson(message: Message) -> None:
    """Handler receives messages with `/remove_lesson` command."""
    logger.info(logs.REMOVE_LESSON_INTENT, message.from_user.full_name)
    with Session(engine) as session:
        user = session.query(User).filter(User.telegram_id == message.from_user.id).first()
        if user:
            lessons = session.query(Lesson).filter(Lesson.user_id == user.id, Lesson.status == "upcoming").all()
            if lessons:
                await message.answer(messages.CHOOSE_LESSON, reply_markup=lessons_to_remove(lessons))
            else:
                await message.answer(messages.NO_LESSONS)
        else:
            await message.answer(messages.NOT_REGISTERED)


@router.callback_query(RemoveLessonCallBack.filter())
async def choose_lesson_to_remove(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_lesson_to_remove` state."""
    logger.info(logs.REMOVE_LESSON_CHOICE, callback.from_user.full_name, callback.data)
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
    await callback.message.answer(messages.NEW_DATE, reply_markup=yes_no())


@router.callback_query(YesNoCallBack.filter())
async def set_new_date(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `set_new_date` state."""
    logger.info(logs.USER_CHOICE, callback.from_user.full_name, callback.data)
    if callback.data == "yes_no:yes":
        await state.set_state(AddLesson.choose_date)
        await callback.message.answer(messages.CHOOSE_NEW_DATE, reply_markup=calendar())
    else:
        await callback.message.answer(messages.LESSON_CANCELED)


@router.message(Command("edit_work_schedule"))
@router.message(F.text == help.EDIT_WORKING_HOURS)
async def edit_work_schedule(message: Message) -> None:
    """Handler receives messages with `/edit_work_schedule` command."""
    if message.from_user.id not in ADMINS:
        await message.answer(messages.PERMISSION_DENIED)
    await message.answer(messages.SHOW_WORK_SCHEDULE, reply_markup=working_hours_keyboard())


@router.callback_query(WeekdayCallBack.filter())
async def choose_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `choose_weekday` state."""
    logger.info(logs.EDIT_WEEKDAY, callback.from_user.full_name, callback.data)
    await state.update_data(weekday=callback.data.replace("choose_weekday:", ""))
    weekday_k6d = working_hours_on_day_keyboard(callback.data.replace("choose_weekday:", ""))
    await callback.message.answer(messages.EDIT_WEEKDAY, reply_markup=weekday_k6d)


@router.callback_query(EditWeekdayCallBack.filter())
async def edit_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    """Handler receives messages with `edit_weekday` state."""
    period = callback.data.replace("edit_weekday:", "")
    state_data = await state.get_data()
    weekday = state_data["weekday"]
    match period:
        case "daystart":
            await state.update_data(period="daystart")
            await callback.message.answer(messages.SEND_NEW_TIME)
        case "dayend":
            await state.update_data(period="dayend")
            await callback.message.answer(messages.SEND_NEW_TIME)
        case "addbreak":
            await state.update_data(period="addbreak")
            await callback.message.answer(messages.SEND_BREAK_TIME)
        case "rmbreak":
            await state.update_data(period="rmbreak")
            async with aiofiles.open(WORK_SCHEDULE_TIMETABLE_PATH) as f:
                data = json.loads(await f.read())
            data[weekday].pop("break")
            async with aiofiles.open(WORK_SCHEDULE_TIMETABLE_PATH, "w") as f:
                await f.write(json.dumps(data))
            await callback.message.answer(messages.BREAK_REMOVED)
        case "breakstart":
            await state.update_data(period="breakstart")
            await callback.message.answer(messages.SEND_NEW_TIME)
        case "breakend":
            await state.update_data(period="breakend")
            await callback.message.answer(messages.SEND_NEW_TIME)
    await state.set_state(NewTime.new_time)


@router.message(NewTime.new_time)
async def new_time(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `new_time` state."""
    state_data = await state.get_data()
    period = state_data["period"]
    try:
        if period == "addbreak":
            start_end = message.text.split("-")
            datetime.strptime(start_end[0], "%H:%M")  # noqa: DTZ007
            datetime.strptime(start_end[1], "%H:%M")  # noqa: DTZ007
        else:
            datetime.strptime(message.text, "%H:%M")  # noqa: DTZ007
    except ValueError:
        await message.answer(messages.INVALID_TIME)
        return
    async with aiofiles.open(WORK_SCHEDULE_TIMETABLE_PATH) as f:
        data = json.loads(await f.read())
    match period:
        case "daystart":
            data[state_data["weekday"]]["start"] = message.text
        case "dayend":
            data[state_data["weekday"]]["end"] = message.text
        case "breakstart":
            data[state_data["weekday"]]["break"]["start"] = message.text
        case "breakend":
            data[state_data["weekday"]]["break"]["end"] = message.text
        case "addbreak":
            start_end = message.text.split("-")
            data[state_data["weekday"]]["break"] = {"start": start_end[0], "end": start_end[1]}
    async with aiofiles.open(WORK_SCHEDULE_TIMETABLE_PATH, "w") as f:
        await f.write(json.dumps(data))
    await message.answer(messages.TIME_UPDATED)
    await state.clear()

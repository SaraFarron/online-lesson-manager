from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.core import config
from src.core.config import TIME_FMT
from src.core.help import Commands
from src.keyboards import Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.models import CancelledRecurrentEvent, Event, RecurrentEvent
from src.repositories import EventHistoryRepo, EventRepo, UserRepo
from src.utils import get_callback_arg, parse_date, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class MoveLesson(StatesGroup):
    scene = "move_lesson"
    command = "/" + scene
    base_callback = scene + "/"
    choose_lesson = f"{base_callback}choose_lesson/"
    move_or_delete = f"{base_callback}move_or_delete/"
    type_date = State()
    choose_time = f"{base_callback}move/choose_time/"
    once_or_forever = f"{base_callback}once_or_forever/"
    choose_weekday = f"{base_callback}choose_weekday/"
    choose_recur_time = f"{base_callback}recur/choose_time/"
    type_recur_date = State()
    type_new_date = State()
    choose_recur_new_time = f"{base_callback}recur/new/choose_time/"


@router.message(Command(MoveLesson.command))
@router.message(F.text == Commands.MOVE_LESSON.value)
async def add_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    user = UserRepo(db).get_by_telegram_id(message.from_user.id, True)

    await state.update_data(user_id=user.telegram_id)
    lessons = EventRepo(db).all_user_lessons(user)
    if lessons:
        await message.answer(replies.CHOOSE_LESSON, reply_markup=Keyboards.choose_lesson(lessons, MoveLesson.choose_lesson))
    else:
        await message.answer(replies.NO_LESSONS)


@router.callback_query(F.data.startswith(MoveLesson.choose_lesson))
async def choose_lesson(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    await state.update_data(lesson=get_callback_arg(callback.data, MoveLesson.choose_lesson))
    await message.answer(replies.MOVE_OR_DELETE, reply_markup=Keyboards.move_or_delete(MoveLesson.move_or_delete))


@router.callback_query(F.data.startswith(MoveLesson.move_or_delete))
async def move_or_delete(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    action = get_callback_arg(callback.data, MoveLesson.move_or_delete)
    if action == "delete" and state_data["lesson"].startswith("e"):
        lesson = EventRepo(db).cancel_event(int(state_data["lesson"].replace("e", "")))
        EventHistoryRepo(db).create(user.username, MoveLesson.scene, "deleted_one_lesson", str(lesson))
        await state.clear()
        await message.answer(replies.LESSON_DELETED)
        return
    if action == "delete" and state_data["lesson"].startswith("re"):
        await state.update_data(action=action)
        await message.answer(replies.DELETE_ONCE_OR_FOREVER, reply_markup=Keyboards.once_or_forever(MoveLesson.once_or_forever))
    elif action == "move" and state_data["lesson"].startswith("e"):
        await state.set_state(MoveLesson.type_date)
        await message.answer(replies.CHOOSE_LESSON_DATE)
    elif action == "move" and state_data["lesson"].startswith("re"):
        await state.update_data(action=action)
        await message.answer(replies.MOVE_ONCE_OR_FOREVER, reply_markup=Keyboards.once_or_forever(MoveLesson.once_or_forever))
    else:
        await message.answer(replies.UNKNOWN_ACTION_ERR)
        await state.clear()

# ---- MOVE ONE LESSON ---- #

@router.message(MoveLesson.type_date)
async def type_date(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    day = parse_date(message.text)
    if day is None:
        await message.answer(replies.WRONG_DATE_FMT)
        await state.set_state(MoveLesson.type_date)
        return
    day = day.date()
    today = datetime.now().date()
    if today > day:
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        await state.set_state(MoveLesson.type_date)
        return

    await state.update_data(day=day)
    available_time = EventRepo(db).available_time(user.executor_id, day)
    available_time = [s for s, e in available_time]
    await message.answer(replies.CHOOSE_TIME, reply_markup=Keyboards.choose_time(available_time, MoveLesson.choose_time))


@router.callback_query(F.data.startswith(MoveLesson.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    day = state_data["day"]
    time = datetime.strptime(
        get_callback_arg(callback.data, MoveLesson.choose_time),
        config.TIME_FMT,
    ).time()

    old_lesson = EventRepo(db).cancel_event(int(state_data["lesson"].replace("e", "")))
    new_lesson = Event(
        user_id=user.id,
        executor_id=user.executor_id,
        event_type=Event.EventTypes.LESSON,
        start=datetime.combine(day, time),
        end=datetime.combine(day, time.replace(hour=time.hour + 1)),
    )
    db.add(new_lesson)
    db.commit()
    await message.answer(replies.LESSON_MOVED)
    EventHistoryRepo(db).create(
        user.username, MoveLesson.scene, "moved_one_lesson", f"{old_lesson} -> {new_lesson}",
    )
    await state.clear()

# ---- RECURRENT LESSON ---- #

@router.callback_query(F.data.startswith(MoveLesson.once_or_forever))
async def once_or_forever(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    mode = get_callback_arg(callback.data, MoveLesson.once_or_forever)
    if mode == "once" and state_data["action"] == "delete":
        await state.set_state(MoveLesson.type_recur_date)
        await message.answer(replies.CHOOSE_CURRENT_LESSON_DATE)
    elif mode == "forever" and state_data["action"] == "delete":
        lesson = db.get(RecurrentEvent, int(state_data["lesson"].replace("re", "")))
        if lesson is None:
            await message.answer(replies.LESSON_NOT_FOUND_ERR)
            await state.clear()
            return
        lesson_str = str(lesson)
        db.delete(lesson)
        db.commit()
        await message.answer(replies.LESSON_DELETED)
        await state.clear()
        EventHistoryRepo(db).create(user.username, MoveLesson.scene, "deleted_recur_lesson", lesson_str)
    elif mode == "once" and state_data["action"] == "move":
        await state.set_state(MoveLesson.type_recur_date)
        await message.answer(replies.CHOOSE_CURRENT_LESSON_DATE)
    elif mode == "forever" and state_data["action"] == "move":
        weekdays = EventRepo(db).available_weekdays(user.id)
        await message.answer(replies.CHOOSE_WEEKDAY, reply_markup=Keyboards.weekdays(weekdays, MoveLesson.choose_weekday))
    else:
        await message.answer(replies.UNKNOWN_ACTION_ERR)
        await state.clear()

# ---- RECURRENT LESSON MOVE FOREVER ---- #

@router.callback_query(F.data.startswith(MoveLesson.choose_weekday))
async def choose_weekday(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    weekday = int(get_callback_arg(callback.data, MoveLesson.choose_weekday))
    await state.update_data(weekday=weekday)
    available_time = EventRepo(db).available_time_weekday(user.executor_id, weekday)
    await message.answer(replies.CHOOSE_TIME, reply_markup=Keyboards.choose_time(available_time, MoveLesson.choose_recur_time))


@router.callback_query(F.data.startswith(MoveLesson.choose_recur_time))
async def choose_recur_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    time = get_callback_arg(callback.data, MoveLesson.choose_recur_time)
    start_of_week = datetime.now().date() - timedelta(days=datetime.now().weekday())
    current_day = start_of_week + timedelta(days=state_data["weekday"])
    start = datetime.combine(current_day, datetime.strptime(time, TIME_FMT).time())
    lesson = RecurrentEvent(
        user_id=user.id,
        executor_id=user.executor_id,
        event_type=RecurrentEvent.EventTypes.LESSON,
        start=start,
        end=start + timedelta(hours=1),
        interval=7,
    )
    db.add(lesson)
    old_lesson = db.get(RecurrentEvent, int(state_data["lesson"].replace("re", "")))
    old_lesson_str = str(old_lesson)
    db.delete(old_lesson)
    db.commit()
    await message.answer(replies.LESSON_MOVED)
    EventHistoryRepo(db).create(
        user.username, MoveLesson.scene, "moved_recur_lesson", f"{old_lesson_str} -> {lesson}",
    )
    await state.clear()

# ---- RECURRENT LESSON ONCE ---- #

@router.message(MoveLesson.type_recur_date)
async def type_recur_date(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    day = parse_date(message.text)
    if day is None:
        await message.answer(replies.WRONG_DATE_FMT)
        await state.set_state(MoveLesson.type_date)
        return
    day = day.date()
    today = datetime.now().date()
    if today > day:
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        await state.set_state(MoveLesson.type_date)
        return

    if state_data["action"] == "delete":
        lesson = db.get(RecurrentEvent, int(state_data["lesson"].replace("re", "")))
        start = datetime.combine(day, lesson.start.time())
        cancel = CancelledRecurrentEvent(
            event_id=lesson.id,
            break_type=CancelledRecurrentEvent.CancelTypes.LESSON_CANCELED,
            start=start,
            end=start + timedelta(hours=1),
        )
        db.add(cancel)
        db.commit()
        await message.answer(replies.LESSON_DELETED)
        EventHistoryRepo(db).create(user.username, MoveLesson.scene, "recur_lesson_deleted", str(cancel))
        await state.clear()
        return

    await state.update_data(day=day)
    await state.set_state(MoveLesson.type_new_date)
    await message.answer(replies.CHOOSE_LESSON_DATE)

# ---- RECURRENT LESSON MOVE ONCE ---- #

@router.message(MoveLesson.type_new_date)
async def type_recur_new_date(message: Message, state: FSMContext, db: Session) -> None:
    message = telegram_checks(message)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    day = parse_date(message.text)
    if day is None:
        await message.answer(replies.WRONG_DATE_FMT)
        await state.set_state(MoveLesson.type_date)
        return
    day = day.date()
    today = datetime.now().date()
    if today > day:
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        await state.set_state(MoveLesson.type_date)
        return

    await state.update_data(new_day=day)
    available_time = EventRepo(db).available_time(user.executor_id, day)
    await message.answer(
        replies.CHOOSE_TIME, reply_markup=Keyboards.choose_time(available_time, MoveLesson.choose_recur_new_time)
    )


@router.callback_query(F.data.startswith(MoveLesson.choose_recur_new_time))
async def choose_recur_new_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserRepo(db).get_by_telegram_id(state_data["user_id"], True)

    time = get_callback_arg(callback.data, MoveLesson.choose_recur_new_time)
    start = datetime.combine(state_data["new_day"], datetime.strptime(time, TIME_FMT).time())
    lesson = Event(
        user_id=user.id,
        executor_id=user.executor_id,
        event_type=Event.EventTypes.MOVED_LESSON,
        start=start,
        end=start + timedelta(hours=1),
        is_reschedule=True,
    )
    cancel = CancelledRecurrentEvent(
        event_id=int(state_data["lesson"].replace("re", "")),
        break_type=CancelledRecurrentEvent.CancelTypes.LESSON_CANCELED,
        start=start,
        end=start + timedelta(hours=1),
    )
    old_lesson_str = f"{Event.EventTypes.LESSON} {state_data['day']} {time}"
    db.add_all([lesson, cancel])
    db.commit()
    await message.answer(replies.LESSON_MOVED)
    EventHistoryRepo(db).create(
        user.username, MoveLesson.scene, "recur_lesson_moved", f"{old_lesson_str} -> {lesson}"
    )
    await state.clear()

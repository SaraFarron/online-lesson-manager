from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from core import config
from core.config import DATE_FMT, DATETIME_FMT, LESSON_SIZE, TIME_FMT, WEEKDAY_MAP
from core.middlewares import DatabaseMiddleware
from db.models import CancelledRecurrentEvent, Event, RecurrentEvent
from db.repositories import EventHistoryRepo, UserRepo
from interface.keyboards import Commands, Keyboards
from interface.messages import replies
from service.lessons import LessonsService
from service.services import EventService, UserService
from service.utils import get_callback_arg, parse_date, send_message

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
async def move_lesson_handler(message: Message, state: FSMContext, db: Session) -> None:
    message, user = UserService(db).check_user(message)

    await state.update_data(user_id=user.telegram_id)
    buttons = LessonsService(db).user_lessons_buttons(user, MoveLesson.choose_lesson)
    if buttons:
        await message.answer(replies.CHOOSE_LESSON, reply_markup=buttons)
    else:
        await message.answer(replies.NO_LESSONS)


@router.callback_query(F.data.startswith(MoveLesson.choose_lesson))
async def choose_lesson(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, _ = UserService(db).check_user_with_id(callback, state_data["user_id"])

    await state.update_data(lesson=get_callback_arg(callback.data, MoveLesson.choose_lesson))
    await message.answer(replies.MOVE_OR_DELETE, reply_markup=Keyboards.move_or_delete(MoveLesson.move_or_delete))


@router.callback_query(F.data.startswith(MoveLesson.move_or_delete))
async def move_or_delete(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"])

    action = get_callback_arg(callback.data, MoveLesson.move_or_delete)

    # Delete one event
    if action == "delete" and state_data["lesson"].startswith("e"):
        lesson = EventService(db).cancel_event(int(state_data["lesson"].replace("e", "")))
        EventHistoryRepo(db).create(user.get_username(), MoveLesson.scene, "deleted_one_lesson", str(lesson))
        await message.answer(replies.LESSON_DELETED)
        executor_tg = UserRepo(db).executor_telegram_id(user)
        await send_message(executor_tg, f"{user.get_username()} отменил(а) {lesson}")
        await state.clear()
        return

    # Delete recurrent event
    if action == "delete" and state_data["lesson"].startswith("re"):
        await state.update_data(action=action)
        await message.answer(
            replies.DELETE_ONCE_OR_FOREVER, reply_markup=Keyboards.once_or_forever(MoveLesson.once_or_forever),
        )

    # Move one event
    elif action == "move" and state_data["lesson"].startswith("e"):
        await state.set_state(MoveLesson.type_date)
        await message.answer(replies.CHOOSE_LESSON_DATE)

    # Move recurrent event
    elif action == "move" and state_data["lesson"].startswith("re"):
        await state.update_data(action=action)
        await message.answer(
            replies.MOVE_ONCE_OR_FOREVER, reply_markup=Keyboards.once_or_forever(MoveLesson.once_or_forever),
        )
    else:
        await message.answer(replies.UNKNOWN_ACTION_ERR)
        await state.clear()

# ---- MOVE ONE LESSON ---- #

@router.message(MoveLesson.type_date)
async def type_date(message: Message, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(message, state_data["user_id"])

    day = parse_date(message.text)
    today = datetime.now().date()
    if day is None:
        await message.answer(replies.WRONG_DATE_FMT)
        await state.set_state(MoveLesson.type_date)
        return
    if today > day:
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        if len(message.text) <= 5:
            await message.answer(replies.ADD_YEAR)
        await state.set_state(MoveLesson.type_date)
        return

    await state.update_data(day=day)
    available_time, _ = EventService(db).available_time(user.executor_id, day)
    if available_time:
        await message.answer(
            replies.CHOOSE_TIME, reply_markup=Keyboards.choose_time(available_time, MoveLesson.choose_time),
        )
    else:
        await message.answer(replies.NO_TIME)
        await state.clear()


@router.callback_query(F.data.startswith(MoveLesson.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"])

    day = state_data["day"]
    time = datetime.strptime(
        get_callback_arg(callback.data, MoveLesson.choose_time),
        config.TIME_FMT,
    ).time()

    old_lesson, new_lesson, created_break = LessonsService(db).move_lesson(
        event_id=int(state_data["lesson"].replace("e", "")),
        user_id=user.id,
        executor_id=user.executor_id,
        day=day,
        time=time,
    )
    await message.answer(replies.LESSON_MOVED)
    EventHistoryRepo(db).create(
        user.get_username(), MoveLesson.scene, "moved_one_lesson", f"{old_lesson} -> {new_lesson}",
    )
    executor_tg = UserRepo(db).executor_telegram_id(user)
    await send_message(executor_tg, f"{user.get_username()} перенес(ла) {old_lesson} -> {new_lesson}")
    if created_break:
        await send_message(executor_tg, f"Автоматически добавлен перерыв на {created_break}")
    await state.clear()

# ---- RECURRENT LESSON ---- #

@router.callback_query(F.data.startswith(MoveLesson.once_or_forever))
async def once_or_forever(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"])

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
        EventHistoryRepo(db).create(user.get_username(), MoveLesson.scene, "deleted_recur_lesson", lesson_str)
        executor_tg = UserRepo(db).executor_telegram_id(user)
        await send_message(executor_tg, f"{user.get_username()} отменил(ла) {lesson_str}")
        await state.clear()

    elif mode == "once" and state_data["action"] == "move":
        await state.set_state(MoveLesson.type_recur_date)
        await message.answer(replies.CHOOSE_CURRENT_LESSON_DATE)

    elif mode == "forever" and state_data["action"] == "move":
        weekdays = EventService(db).available_weekdays(user.executor_id)
        await message.answer(
            replies.CHOOSE_WEEKDAY,
            reply_markup=Keyboards.weekdays(weekdays, MoveLesson.choose_weekday),
        )
    else:
        await message.answer(replies.UNKNOWN_ACTION_ERR)
        await state.clear()

# ---- RECURRENT LESSON MOVE FOREVER ---- #

@router.callback_query(F.data.startswith(MoveLesson.choose_weekday))
async def choose_weekday(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"])

    weekday = int(get_callback_arg(callback.data, MoveLesson.choose_weekday))
    await state.update_data(weekday=weekday)
    available_time, _ = EventService(db).available_time_weekday(user.executor_id, weekday)
    await message.answer(
        replies.CHOOSE_TIME,
        reply_markup=Keyboards.choose_time(available_time, MoveLesson.choose_recur_time),
    )


@router.callback_query(F.data.startswith(MoveLesson.choose_recur_time))
async def choose_recur_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"])

    now = datetime.now()
    time = get_callback_arg(callback.data, MoveLesson.choose_recur_time)
    start_of_week = now.date() - timedelta(days=now.weekday())
    current_day = start_of_week + timedelta(days=state_data["weekday"])
    start = datetime.combine(current_day, datetime.strptime(time, TIME_FMT).time())
    old_lesson_str, lesson, created_break = LessonsService(db).update_recurrent_lesson(
        event_id=int(state_data["lesson"].replace("re", "")),
        user_id=user.id,
        executor_id=user.executor_id,
        start=start,
    )
    await message.answer(replies.LESSON_MOVED)
    username = user.username if user.username else user.full_name
    EventHistoryRepo(db).create(
        username, MoveLesson.scene, "moved_recur_lesson", f"{old_lesson_str} -> {lesson}",
    )
    executor_tg = UserRepo(db).executor_telegram_id(user)
    await send_message(executor_tg, f"{username} перенес(ла) {old_lesson_str} -> {lesson}")
    if created_break:
        await send_message(executor_tg, f"Автоматически добавлен перерыв на {created_break}")
    await state.clear()

# ---- RECURRENT LESSON ACTION ONCE ---- #

@router.message(MoveLesson.type_recur_date)
async def type_recur_date(message: Message, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(message, state_data["user_id"])

    day = parse_date(message.text)
    today = datetime.now().date()
    if day is None:
        await message.answer(replies.WRONG_DATE_FMT)
        await state.set_state(MoveLesson.type_date)
        return
    if today > day:
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        if len(message.text) <= 5:
            await message.answer(replies.ADD_YEAR)
        await state.set_state(MoveLesson.type_date)
        return

    lesson = db.get(RecurrentEvent, int(state_data["lesson"].replace("re", "")))
    if day.weekday() != lesson.start.weekday():
        msg = f"В {WEEKDAY_MAP[day.weekday()]['long']} нет этого занятия"
        await message.answer(msg)
        await state.set_state(MoveLesson.type_date)
        return

    if state_data["action"] == "delete":
        start = datetime.combine(day, lesson.start.time())
        cancel = CancelledRecurrentEvent(
            event_id=lesson.id,
            break_type=CancelledRecurrentEvent.CancelTypes.LESSON_CANCELED,
            start=start,
            end=start + LESSON_SIZE,
        )
        db.add(cancel)
        db.commit()
        await message.answer(replies.LESSON_DELETED)
        username = user.username if user.username else user.full_name
        EventHistoryRepo(db).create(username, MoveLesson.scene, "recur_lesson_deleted", str(lesson))
        executor_tg = UserRepo(db).executor_telegram_id(user)
        await send_message(executor_tg, f"{username} отменил(ла) {lesson} на {datetime.strftime(day, DATE_FMT)}")
        await state.clear()
        return

    await state.update_data(day=datetime.strftime(day, DATE_FMT), old_time=datetime.strftime(lesson.start, TIME_FMT))
    await state.set_state(MoveLesson.type_new_date)
    await message.answer(replies.CHOOSE_LESSON_DATE)

# ---- RECURRENT LESSON MOVE ONCE ---- #

@router.message(MoveLesson.type_new_date)
async def type_recur_new_date(message: Message, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(message, state_data["user_id"])

    day = parse_date(message.text)
    if day is None:
        await message.answer(replies.WRONG_DATE_FMT)
        await state.set_state(MoveLesson.type_date)
        return

    today = datetime.now().date()
    if today > day:
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        if len(message.text) <= 5:
            await message.answer(replies.ADD_YEAR)
        await state.set_state(MoveLesson.type_date)
        return

    await state.update_data(new_day=day)
    available_time, _ = EventService(db).available_time(user.executor_id, day)
    if available_time:
        await message.answer(
            replies.CHOOSE_TIME, reply_markup=Keyboards.choose_time(available_time, MoveLesson.choose_recur_new_time),
        )
    else:
        await message.answer(replies.NO_TIME)
        await state.clear()


@router.callback_query(F.data.startswith(MoveLesson.choose_recur_new_time))
async def choose_recur_new_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    state_data = await state.get_data()
    message, user = UserService(db).check_user_with_id(callback, state_data["user_id"])

    time = get_callback_arg(callback.data, MoveLesson.choose_recur_new_time)
    old_start = datetime.strptime(f"{state_data['day']} {state_data['old_time']}", DATETIME_FMT)
    lesson, created_break = LessonsService(db).create_lesson(
        user_id=user.id,
        executor_id=user.executor_id,
        date=state_data["new_day"],
        time=time,
    )
    cancel = CancelledRecurrentEvent(
        event_id=int(state_data["lesson"].replace("re", "")),
        break_type=CancelledRecurrentEvent.CancelTypes.LESSON_CANCELED,
        start=old_start,
        end=old_start + LESSON_SIZE,
    )
    old_lesson_str = f"{Event.EventTypes.LESSON} {state_data['day']} в {state_data['old_time']}"
    db.add_all([lesson, cancel])
    db.commit()
    await message.answer(replies.LESSON_MOVED)
    username = user.username if user.username else user.full_name
    EventHistoryRepo(db).create(
        username, MoveLesson.scene, "recur_lesson_moved", f"{old_lesson_str} -> {lesson}",
    )
    executor_tg = UserRepo(db).executor_telegram_id(user)
    await send_message(executor_tg, f"{username} перенес(ла) {old_lesson_str} -> {lesson}")
    if created_break:
        await send_message(executor_tg, f"Автоматически добавлен перерыв на {created_break}")
    await state.clear()

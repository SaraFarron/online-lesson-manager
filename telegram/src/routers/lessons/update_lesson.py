from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.core import config
from src.core.config import DATE_FMT, DATETIME_FMT, LESSON_SIZE, TIME_FMT, WEEKDAY_MAP
from src.messages import replies
from src.routers.utils import student_permission
from src.service import DeleteLessonService, MoveLessonService, UpdateLessonService
from src.states import UpdateLesson
from src.utils import get_callback_arg, parse_date, send_message

router = Router()


@router.message(Command(UpdateLesson.command))
@router.message(F.text == UpdateLesson.text)
async def move_lesson_handler(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return

    await state.update_data(user_id=user.telegram_id)
    service = UpdateLessonService(message, state)
    await service.get_lesson()


@router.callback_query(F.data.startswith(UpdateLesson.choose_lesson))
async def choose_lesson(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    service = UpdateLessonService(message, state, callback)
    await service.choose_action()


@router.callback_query(F.data.startswith(UpdateLesson.move_or_delete))
async def move_or_delete(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return

    action = get_callback_arg(callback.data, UpdateLesson.move_or_delete)

    match action:
        case "move":
            service = MoveLessonService(message, state, callback)
        case "delete":
            service = DeleteLessonService(message, state, callback)
        case _:
            await message.answer(replies.UNKNOWN_ACTION_ERR)
            await state.clear()
            return

    await service.perform_action()


# ---- MOVE ONE LESSON ---- #


@router.message(UpdateLesson.type_date)
async def type_date(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return

    day = parse_date(message.text)
    today = datetime.now().date()
    if day is None:
        await message.answer(replies.WRONG_DATE_FMT)
        await state.set_state(UpdateLesson.type_date)
        return
    if today > day:
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        if len(message.text) <= 5:
            await message.answer(replies.ADD_YEAR)
        await state.set_state(UpdateLesson.type_date)
        return

    await state.update_data(day=day)
    available_time, _ = EventService(db).available_time(user.executor_id, day)
    if available_time:
        await message.answer(
            replies.CHOOSE_TIME,
            reply_markup=Keyboards.choose_time(available_time, UpdateLesson.choose_time),
        )
    else:
        await message.answer(replies.NO_TIME)
        await state.clear()


@router.callback_query(F.data.startswith(UpdateLesson.choose_time))
async def choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return
    state_data = await state.get_data()

    day = state_data["day"]
    time = datetime.strptime(
        get_callback_arg(callback.data, UpdateLesson.choose_time),
        config.TIME_FMT,
    ).time()

    old_lesson, new_lesson = LessonsService(db).move_lesson(
        event_id=int(state_data["lesson"].replace("e", "")),
        user_id=user.id,
        executor_id=user.executor_id,
        day=day,
        time=time,
    )
    await message.answer(replies.LESSON_MOVED)
    EventHistoryRepo(db).create(
        user.get_username(),
        MoveLesson.scene,
        "moved_one_lesson",
        f"{old_lesson} -> {new_lesson}",
    )
    executor_tg = UserRepo(db).executor_telegram_id(user)
    await send_message(executor_tg, f"{user.get_username()} перенес(ла) {old_lesson} -> {new_lesson}")
    await auto_place_work_breaks(db, user, day, executor_tg)
    await state.clear()


# ---- RECURRENT LESSON ---- #


@router.callback_query(F.data.startswith(UpdateLesson.once_or_forever))
async def once_or_forever(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return
    state_data = await state.get_data()

    mode = get_callback_arg(callback.data, UpdateLesson.once_or_forever)
    if mode == "once" and state_data["action"] == "delete":
        await state.set_state(UpdateLesson.type_recur_date)
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
        EventHistoryRepo(db).create(user.get_username(), UpdateLesson.scene, "deleted_recur_lesson", lesson_str)
        executor_tg = UserRepo(db).executor_telegram_id(user)
        await send_message(executor_tg, f"{user.get_username()} отменил(ла) {lesson_str}")
        await state.clear()

    elif mode == "once" and state_data["action"] == "move":
        await state.set_state(UpdateLesson.type_recur_date)
        await message.answer(replies.CHOOSE_CURRENT_LESSON_DATE)

    elif mode == "forever" and state_data["action"] == "move":
        weekdays = EventService(db).available_weekdays(user.executor_id)
        await message.answer(
            replies.CHOOSE_WEEKDAY,
            reply_markup=Keyboards.weekdays(weekdays, UpdateLesson.choose_weekday),
        )
    else:
        await message.answer(replies.UNKNOWN_ACTION_ERR)
        await state.clear()


# ---- RECURRENT LESSON MOVE FOREVER ---- #


@router.callback_query(F.data.startswith(UpdateLesson.choose_weekday))
async def choose_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return
    state_data = await state.get_data()

    weekday = int(get_callback_arg(callback.data, UpdateLesson.choose_weekday))
    await state.update_data(weekday=weekday)
    available_time, _ = EventService(db).available_time_weekday(user.executor_id, weekday)
    await message.answer(
        replies.CHOOSE_TIME,
        reply_markup=Keyboards.choose_time(available_time, UpdateLesson.choose_recur_time),
    )


@router.callback_query(F.data.startswith(UpdateLesson.choose_recur_time))
async def choose_recur_time(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return
    state_data = await state.get_data()

    now = datetime.now()
    time = get_callback_arg(callback.data, UpdateLesson.choose_recur_time)
    start_of_week = now.date() - timedelta(days=now.weekday())
    current_day = start_of_week + timedelta(days=state_data["weekday"])
    start = datetime.combine(current_day, datetime.strptime(time, TIME_FMT).time())
    old_lesson_str, lesson = LessonsService(db).update_recurrent_lesson(
        event_id=int(state_data["lesson"].replace("re", "")),
        user_id=user.id,
        executor_id=user.executor_id,
        start=start,
    )
    await message.answer(replies.LESSON_MOVED)
    username = user.username if user.username else user.full_name
    EventHistoryRepo(db).create(
        username,
        UpdateLesson.scene,
        "moved_recur_lesson",
        f"{old_lesson_str} -> {lesson}",
    )
    executor_tg = UserRepo(db).executor_telegram_id(user)
    await send_message(executor_tg, f"{username} перенес(ла) {old_lesson_str} -> {lesson}")
    await auto_place_work_breaks(db, user, start, executor_tg)
    await state.clear()


# ---- RECURRENT LESSON ACTION ONCE ---- #


@router.message(UpdateLesson.type_recur_date)
async def type_recur_date(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return
    state_data = await state.get_data()

    day = parse_date(message.text)
    today = datetime.now().date()
    if day is None:
        await message.answer(replies.WRONG_DATE_FMT)
        await state.set_state(UpdateLesson.type_date)
        return
    if today > day:
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        if len(message.text) <= 5:
            await message.answer(replies.ADD_YEAR)
        await state.set_state(UpdateLesson.type_date)
        return

    event_id = int(state_data["lesson"].replace("re", ""))
    lesson = db.get(RecurrentEvent, event_id)
    if day.weekday() != lesson.start.weekday():
        msg = f"В {WEEKDAY_MAP[day.weekday()]['long']} нет этого занятия"
        await message.answer(msg)
        await state.set_state(UpdateLesson.type_date)
        return

    existing_cancel = cancel_for_event(db, event_id, day)
    if existing_cancel:
        await message.answer(replies.LESSON_ALREADY_CANCELED)
        await state.clear()
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
        EventHistoryRepo(db).create(username, UpdateLesson.scene, "recur_lesson_deleted", str(lesson))
        executor_tg = UserRepo(db).executor_telegram_id(user)
        await send_message(executor_tg, f"{username} отменил(ла) {lesson} на {datetime.strftime(day, DATE_FMT)}")
        await state.clear()
        return

    await state.update_data(day=datetime.strftime(day, DATE_FMT), old_time=datetime.strftime(lesson.start, TIME_FMT))
    await state.set_state(UpdateLesson.type_new_date)
    await message.answer(replies.CHOOSE_LESSON_DATE)


# ---- RECURRENT LESSON MOVE ONCE ---- #


@router.message(UpdateLesson.type_new_date)
async def type_recur_new_date(message: Message, state: FSMContext) -> None:
    user, message = await student_permission(message)
    if user is None:
        return
    state_data = await state.get_data()

    day = parse_date(message.text)
    if day is None:
        await message.answer(replies.WRONG_DATE_FMT)
        await state.set_state(UpdateLesson.type_date)
        return

    today = datetime.now().date()
    if today > day:
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        if len(message.text) <= 5:
            await message.answer(replies.ADD_YEAR)
        await state.set_state(UpdateLesson.type_date)
        return

    await state.update_data(new_day=day)
    available_time, _ = EventService(db).available_time(user.executor_id, day)
    if available_time:
        await message.answer(
            replies.CHOOSE_TIME,
            reply_markup=Keyboards.choose_time(available_time, UpdateLesson.choose_recur_new_time),
        )
    else:
        await message.answer(replies.NO_TIME)
        await state.clear()


@router.callback_query(F.data.startswith(UpdateLesson.choose_recur_new_time))
async def choose_recur_new_time(callback: CallbackQuery, state: FSMContext) -> None:
    user, message = await student_permission(callback)
    if user is None:
        return
    state_data = await state.get_data()

    time = get_callback_arg(callback.data, UpdateLesson.choose_recur_new_time)
    old_start = datetime.strptime(f"{state_data['day']} {state_data['old_time']}", DATETIME_FMT)
    lesson = LessonsService(db).create_lesson(
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
        username,
        UpdateLesson.scene,
        "recur_lesson_moved",
        f"{old_lesson_str} -> {lesson}",
    )
    executor_tg = UserRepo(db).executor_telegram_id(user)
    await send_message(executor_tg, f"{username} перенес(ла) {old_lesson_str} -> {lesson}")
    await auto_place_work_breaks(db, user, state_data["new_day"], executor_tg)
    await state.clear()

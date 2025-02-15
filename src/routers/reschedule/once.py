from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from messages import buttons, replies
from models import Reschedule, ScheduledLesson, User, Lesson
from repositories import RescheduleRepo, ScheduledLessonRepo, UserRepo, LessonRepo
from routers.reschedule.config import ORL_RS_CALLBACK, ORL_START_CALLBACK, ORL_ONE_START_CALLBACK, router
from service import Schedule
from utils import calc_end_time, inline_keyboard, send_message


class ChooseNewDateTime(StatesGroup):
    date = State()
    time = State()


class Callbacks:
    CHOOSE_WEEKDAY = "orl_choose_weekday:"
    CONFIRM = "orl_confirm:"
    CHOOSE_DATE = "orl_choose_date:"
    CHOOSE_TIME = "orl_choose_time:"


@router.callback_query(F.data.startswith(ORL_ONE_START_CALLBACK))
async def orl_type_date(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    if not isinstance(callback.message, Message):
        return
    event: Lesson | None = LessonRepo(db).get(int(callback.data.split(":")[-1]))
    await state.update_data(date=event.date, event=event, user_telegram_id=event.user.telegram_id)
    keyboard = inline_keyboard(
        [
            (buttons.CANCEL_LESSON, Callbacks.CONFIRM),
            (buttons.CHOOSE_NEW_DATE, Callbacks.CHOOSE_DATE),
        ],
    ).as_markup()
    await callback.message.answer(replies.CONFIRM, reply_markup=keyboard)



@router.callback_query(F.data.startswith(ORL_START_CALLBACK))
async def orl_type_date(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    state_data = await state.get_data()
    lesson: ScheduledLesson | None = ScheduledLessonRepo(db).get(state_data["lesson"])
    if not isinstance(callback.message, Message):
        return
    if lesson:
        await state.update_data(
            event=lesson,
            user_id=lesson.user_id,
            user_telegram_id=lesson.user.telegram_id,
        )
        await state.set_state(ChooseNewDateTime.date)
        await callback.message.answer(replies.TYPE_NEW_DATE)
    else:
        await state.clear()
        await callback.message.answer("Произошла непредвиденная ошибка")
        await callback.message.answer(replies.ACTION_CANCELLED)


@router.callback_query(F.data.startswith(ORL_RS_CALLBACK))
async def orl_rs_cancel_or_reschedule(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    if not isinstance(callback.message, Message):
        return
    event: Reschedule = RescheduleRepo(db).get(int(callback.data.split(":")[-1]))  # type: ignore  # noqa: PGH003
    await state.update_data(date=event.source_date, event=event, user_telegram_id=event.user.telegram_id)
    keyboard = inline_keyboard(
        [
            (buttons.CANCEL_LESSON, Callbacks.CONFIRM),
            (buttons.CHOOSE_NEW_DATE, Callbacks.CHOOSE_DATE),
        ],
    ).as_markup()
    await callback.message.answer(replies.CONFIRM, reply_markup=keyboard)


@router.message(ChooseNewDateTime.date)
async def orl_cancel_or_reschedule(message: Message, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    now = datetime.now(tz=config.TIMEZONE)
    try:
        date = datetime.strptime(message.text if message.text else "", "%d-%m-%Y")  # noqa: DTZ007
    except ValueError:
        await state.set_state(ChooseNewDateTime.date)
        await message.answer(replies.WRONG_DATE)
        return
    if date.date() < now.date():
        await state.set_state(ChooseNewDateTime.date)
        await message.answer(replies.CHOOSE_LESSON_IN_FUTURE)
        return
    state_data = await state.get_data()
    reschedules = db.query(Reschedule).filter(Reschedule.source_date == date.date()).all()
    rshs: list[Reschedule] = []
    for r in reschedules:
        if r.source is None:
            db.delete(r)
            db.commit()
        rshs.append(r)
    event = state_data["event"]
    if isinstance(event, ScheduledLesson) and event.id in [r.source.id for r in rshs]:
        await message.answer(replies.ALREADY_CANCELED)
        await state.clear()
        await message.answer("Операция отменена")
        return
    if isinstance(event, ScheduledLesson):
        if date.date() == now.date() and Schedule(db).is_too_late_to_cancel(event.start_time, now.date()):
            await message.answer(replies.CHOOSE_REASONABLE_TIME)
            await state.set_state(ChooseNewDateTime.date)
            return
        right_weekday = ScheduledLessonRepo(db).get(state_data["lesson"]).weekday
        if date.weekday() != right_weekday:
            await state.set_state(ChooseNewDateTime.date)
            await message.answer(
                replies.CHOOSE_RIGHT_WEEKDAY
                % (config.WEEKDAY_MAP_FULL[date.weekday()], config.WEEKDAY_MAP_FULL[right_weekday]),
            )
            return

    await state.update_data(date=date)
    keyboard = inline_keyboard(
        [
            (buttons.CANCEL_LESSON, Callbacks.CONFIRM),
            (buttons.CHOOSE_NEW_DATE, Callbacks.CHOOSE_DATE),
        ],
    ).as_markup()
    await message.answer(replies.CONFIRM, reply_markup=keyboard)


@router.callback_query(F.data == Callbacks.CONFIRM)
async def orl_cancel_lesson(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_confirm` state."""
    if not isinstance(callback.message, Message):
        return
    state_data = await state.get_data()
    user: User = UserRepo(db).get(state_data["user_id"])
    if isinstance(state_data["event"], Reschedule):
        event = RescheduleRepo(db).get(state_data["event"].id)
        db.delete(event)
    elif isinstance(state_data["event"], Lesson):
        event = LessonRepo(db).get(state_data["event"].id)
        db.delete(event)
    else:
        event: ScheduledLesson = ScheduledLessonRepo(db).get(state_data["lesson"])
        reschedule = Reschedule(
            user=user,
            source=event,
            source_date=state_data["date"],
            start_time=event.start_time,
            end_time=event.end_time,
        )
        db.add(reschedule)
    message = replies.USER_CANCELED_SL % (
        user.username_dog,
        state_data["date"].strftime("%d-%m-%Y"),
        event.st_str,
    )
    db.commit()
    await send_message(user.teacher.telegram_id, message)
    await state.clear()
    await callback.message.answer(replies.CANCELED)


@router.callback_query(F.data == Callbacks.CHOOSE_DATE)
async def orl_choose_new_date(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_choose_date` state."""
    if not isinstance(callback.message, Message):
        return
    state_data = await state.get_data()
    user = UserRepo(db).get(state_data["user_id"])
    if not user:
        await state.clear()
        raise PermissionError
    schedule = Schedule(db)
    weekends_str = ", ".join([config.WEEKDAY_MAP_FULL[w] for w in schedule.available_weekdays(user)])
    await state.set_state(ChooseNewDateTime.time)
    await callback.message.answer(replies.CHOOSE_DATE % weekends_str)


@router.message(ChooseNewDateTime.time)
@router.callback_query(F.data.startswith(Callbacks.CHOOSE_WEEKDAY))
async def orl_choose_time(message: Message, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_choose_time` state."""
    state_data = await state.get_data()
    try:
        date = datetime.strptime(message.text if message.text else "", "%d-%m-%Y")  # noqa: DTZ007
    except ValueError:
        await state.set_state(ChooseNewDateTime.time)
        await message.answer(replies.WRONG_DATE)
        return
    if date.date() < datetime.now(tz=config.TIMEZONE).date():
        await state.set_state(ChooseNewDateTime.time)
        await message.answer(replies.CHOOSE_FUTURE_DATE)
        return
    await state.update_data(new_date=date)

    user = UserRepo(db).get(state_data["user_id"])
    if not user:
        await state.clear()
        raise PermissionError

    schedule = Schedule(db)
    weekday = date if isinstance(date, int) else date.weekday()
    if weekday not in schedule.available_weekdays(user):
        await message.answer(replies.WRONG_WEEKDAY % config.WEEKDAY_MAP_FULL[weekday])
        return
    await state.update_data(new_date=date)
    await state.set_state(ChooseNewDateTime.time)
    available_time = schedule.available_time(user, date.date())
    if not available_time:
        await message.answer(replies.NO_AVAILABLE_TIME)
        await state.clear()
        return
    buttons = [(t.strftime("%H:%M"), Callbacks.CHOOSE_TIME + t.strftime("%H.%M")) for t in available_time]
    keyboard = inline_keyboard(buttons)
    keyboard.adjust(2, repeat=True)
    await message.answer(replies.CHOOSE_TIME, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_TIME))
async def reschedule_lesson_create_reschedule(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_create_reschedule` state."""
    if not isinstance(callback.message, Message):
        return
    state_data = await state.get_data()
    time = datetime.strptime(callback.data.split(":")[1], "%H.%M").time()  # type: ignore # noqa: DTZ007, PGH003
    now = datetime.now(tz=config.TIMEZONE)
    if state_data["new_date"].date() == now.date():
        if time < now.time():
            await callback.message.answer(replies.CHOOSE_FUTURE_DATE)
            return
        if time < now.time().replace(hour=now.time().hour + config.HRS_TO_CANCEL):
            await state.clear()
            await callback.message.answer(replies.CHOOSE_REASONABLE_TIME)
            await callback.message.answer("Операция отменена")
            return
    user: User = UserRepo(db).get(state_data["user_id"])
    event = state_data["event"]
    if isinstance(event, Reschedule):
        event: Reschedule = RescheduleRepo(db).get(event.id)
        old_date, old_time = event.source_date, event.start_time
        event.date = state_data["new_date"]
        event.start_time = time
        event.end_time = calc_end_time(time)
    elif isinstance(event, Lesson):
        event: Lesson = LessonRepo(db).get(event.id)
        old_date, old_time = event.date, event.start_time
        event.date = state_data["new_date"]
        event.start_time = time
        event.end_time = calc_end_time(time)
    else:
        sl: ScheduledLesson = ScheduledLessonRepo(db).get(state_data["lesson"])
        reschedule = RescheduleRepo(db).new(user, sl, state_data["date"], state_data["new_date"], time)
        old_date, old_time = reschedule.source_date.strftime("%d-%m-%Y"), sl.st_str
    message = replies.USER_MOVED_SL % (
        user.username_dog,
        old_date,
        old_time,
        state_data["new_date"].strftime("%d-%m-%Y"),
        time.strftime("%H:%M"),
    )
    db.commit()
    await send_message(user.teacher.telegram_id, message)
    await callback.message.answer(replies.LESSON_ADDED)
    await state.clear()

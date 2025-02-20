from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from messages import buttons, replies
from models import Reschedule, ScheduledLesson, Lesson
from repositories import RescheduleRepo, ScheduledLessonRepo
from routers.reschedule.config import ORL_RS_CALLBACK, ORL_START_CALLBACK, ORL_ONE_START_CALLBACK, router
from utils import inline_keyboard


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
    message = callback.message
    if not isinstance(message, Message):
        return

    service = Service(db)
    service.get_user(message.from_user.id)

    event = service.get_lesson(int(callback.data.split(":")[-1]))
    await state.update_data(date=event.date, event=event)
    keyboard = inline_keyboard(
        [
            (buttons.CANCEL_LESSON, Callbacks.CONFIRM),
            (buttons.CHOOSE_NEW_DATE, Callbacks.CHOOSE_DATE),
        ],
    ).as_markup()

    await message.answer(replies.CONFIRM, reply_markup=keyboard)


@router.callback_query(F.data.startswith(ORL_START_CALLBACK))
async def orl_type_date(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    message = callback.message
    if not isinstance(message, Message):
        return

    service = Service(db)
    service.get_user(message.from_user.id)

    state_data = await state.get_data()
    lesson = service.get_sl(state_data["lesson"])
    if lesson:
        await state.update_data(event=lesson)
        await state.set_state(ChooseNewDateTime.date)
        await message.answer(replies.TYPE_NEW_DATE)
    else:
        await state.clear()
        await message.answer("Произошла непредвиденная ошибка")
        await message.answer(replies.ACTION_CANCELLED)


@router.callback_query(F.data.startswith(ORL_RS_CALLBACK))
async def orl_rs_cancel_or_reschedule(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    message = callback.message
    if not isinstance(message, Message):
        return

    service = Service(db)
    service.get_user(message.from_user.id)

    event = service.get_reschedule(int(callback.data.split(":")[-1]))  # type: ignore  # noqa: PGH003
    await state.update_data(date=event.source_date, event=event)
    keyboard = inline_keyboard(
        [
            (buttons.CANCEL_LESSON, Callbacks.CONFIRM),
            (buttons.CHOOSE_NEW_DATE, Callbacks.CHOOSE_DATE),
        ],
    ).as_markup()

    await message.answer(replies.CONFIRM, reply_markup=keyboard)


@router.message(ChooseNewDateTime.date)
async def orl_cancel_or_reschedule(message: Message, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    service = Service(db)
    user = service.get_user(message.from_user.id)

    try:
        date = datetime.strptime(message.text if message.text else "", "%d-%m-%Y")  # noqa: DTZ007
    except ValueError:
        await state.set_state(ChooseNewDateTime.date)
        await message.answer(replies.WRONG_DATE)
        return

    now = datetime.now(tz=config.TIMEZONE)
    if date.date() < now.date():
        await state.set_state(ChooseNewDateTime.date)
        await message.answer(replies.CHOOSE_LESSON_IN_FUTURE)
        return

    state_data = await state.get_data()
    reschedules = service.get_reschedules(user, state_data["lesson"])
    event = state_data["event"]
    if isinstance(event, ScheduledLesson) and event.id in [r.source.id for r in reschedules]:
        await message.answer(replies.ALREADY_CANCELED)
        await state.clear()
        await message.answer("Операция отменена")
        return

    if isinstance(event, ScheduledLesson):
        if service.is_cancellable(user, event):
            await message.answer(replies.CHOOSE_REASONABLE_TIME)
            await state.set_state(ChooseNewDateTime.date)
            return
        right_weekday = service.get_sl(state_data["lesson"]).weekday
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
    message = callback.message
    if not isinstance(message, Message):
        return

    service = Service(db)
    user = service.get_user(message.from_user.id)

    state_data = await state.get_data()
    if isinstance(state_data["event"], Reschedule):
        service.delete_reschedule(user, state_data["event"])
    elif isinstance(state_data["event"], Lesson):
        service.delete_lesson(user, state_data["event"])
    else:
        service.move_one_sl(user, state_data["event"], state_data["date"])
    db.commit()

    await state.clear()
    await message.answer(replies.CANCELED)


@router.callback_query(F.data == Callbacks.CHOOSE_DATE)
async def orl_choose_new_date(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_choose_date` state."""
    message = callback.message
    if not isinstance(message, Message):
        return

    service = Service(db)
    user = service.get_user(message.from_user.id)

    weekends_str = ", ".join([config.WEEKDAY_MAP_FULL[w] for w in service.available_weekdays(user)])
    await state.set_state(ChooseNewDateTime.time)

    await message.answer(replies.CHOOSE_DATE % weekends_str)


@router.message(ChooseNewDateTime.time)
@router.callback_query(F.data.startswith(Callbacks.CHOOSE_WEEKDAY))
async def orl_choose_time(message: Message, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_choose_time` state."""
    service = Service(db)
    user = service.get_user(message.from_user.id)

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

    weekday = date if isinstance(date, int) else date.weekday()
    if weekday not in service.available_weekdays(user):
        await message.answer(replies.WRONG_WEEKDAY % config.WEEKDAY_MAP_FULL[weekday])
        return

    await state.update_data(new_date=date)
    await state.set_state(ChooseNewDateTime.time)

    available_time = service.available_time(user, date.date())
    if not available_time:
        await message.answer(replies.NO_AVAILABLE_TIME)
        await state.clear()
        return
    buttons = [(t.strftime("%H:%M"), Callbacks.CHOOSE_TIME + t.strftime("%H.%M")) for t in available_time]
    keyboard = inline_keyboard(buttons)
    await message.answer(replies.CHOOSE_TIME, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_TIME))
async def reschedule_lesson_create_reschedule(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_create_reschedule` state."""
    message = callback.message
    if not isinstance(message, Message):
        return

    service = Service(db)
    user = service.get_user(message.from_user.id)

    state_data = await state.get_data()
    time = datetime.strptime(callback.data.split(":")[1], "%H.%M").time()  # type: ignore # noqa: DTZ007, PGH003
    now = datetime.now(tz=config.TIMEZONE)
    if state_data["new_date"].date() == now.date():
        if time < now.time():
            await message.answer(replies.CHOOSE_FUTURE_DATE)
            return

        if time < now.time().replace(hour=now.time().hour + config.HRS_TO_CANCEL):
            await state.clear()
            await message.answer(replies.CHOOSE_REASONABLE_TIME)
            await message.answer("Операция отменена")
            return

    event = state_data["event"]
    if isinstance(event, Reschedule):
        service.move_reschedule(user, state_data["new_date"], time)
    elif isinstance(event, Lesson):
        service.move_lesson(user, state_data["new_date"], time)
    else:
        # WTF
        sl: ScheduledLesson = ScheduledLessonRepo(db).get(state_data["lesson"])
        reschedule = RescheduleRepo(db).new(user, sl, state_data["date"], state_data["new_date"], time)

    db.commit()

    await state.clear()
    await message.answer(replies.LESSON_ADDED)

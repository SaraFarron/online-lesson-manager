from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from errors import AiogramTelegramError
from messages import buttons, replies
from models import Reschedule, ScheduledLesson, User
from repositories import ScheduledLessonRepo, UserRepo
from routers.reschedule.config import FRL_START_CALLBACK, router
from service import SecondFunctions
from utils import MAX_HOUR, inline_keyboard, send_message


class Callbacks:
    CHOOSE_WEEKDAY = "frl_choose_weekday:"
    CONFIRM = "frl_confirm:"
    CHOOSE_DATE = "frl_choose_date:"
    CHOOSE_TIME = "frl_choose_time:"


@router.callback_query(F.data.startswith(FRL_START_CALLBACK))
async def frl_cancel_or_reschedule(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschesule_lesson_choose_sl` state."""
    state_data = await state.get_data()
    lesson: ScheduledLesson = ScheduledLessonRepo(db).get(state_data["lesson"])
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    if lesson:
        await state.update_data(
            lesson=state_data["lesson"],
            user_id=lesson.user_id,
            user_telegram_id=lesson.user.telegram_id,
        )
        keyboard = inline_keyboard(
            [
                (buttons.CANCEL_LESSON, Callbacks.CONFIRM),
                (buttons.CHOOSE_NEW_DATE, Callbacks.CHOOSE_DATE),
            ],
        ).as_markup()
        await callback.message.answer(replies.CONFRIM, reply_markup=keyboard)


@router.callback_query(F.data == Callbacks.CONFIRM)
async def frl_delete_sl(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_confirm` state."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    state_data = await state.get_data()
    sl: ScheduledLesson = ScheduledLessonRepo(db).get(state_data["lesson"])
    user: User = UserRepo(db).get(state_data["user_id"])
    message = replies.USER_DELETED_SL % (user.username_dog, sl.weekday_full_str, sl.st_str)
    # Delete all reschedules for this lesson in order to prevent errors
    reschedules_to_delete = db.query(Reschedule).filter_by(source=sl).all()
    for reschedule in reschedules_to_delete:
        db.delete(reschedule)

    db.delete(sl)
    db.commit()
    await send_message(user.teacher.telegram_id, message)
    await state.clear()
    await callback.message.answer(replies.CANCELED)


@router.callback_query(F.data == Callbacks.CHOOSE_DATE)
async def frl_choose_weekday(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_choose_date` state."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    state_data = await state.get_data()
    schedule = SecondFunctions(db, state_data["user_telegram_id"])
    weekdays = [(config.WEEKDAY_MAP[w], Callbacks.CHOOSE_WEEKDAY + str(w)) for w in schedule.available_weekdays()]
    keyboard = inline_keyboard(weekdays)
    await callback.message.answer(replies.CHOOSE_WEEKDAY, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_WEEKDAY))
async def frl_choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_choose_time` state."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    state_data = await state.get_data()
    weekday = int(callback.data.split(":")[1])  # type: ignore  # noqa: PGH003

    schedule = SecondFunctions(db, state_data["user_telegram_id"])
    if weekday not in schedule.available_weekdays():
        await callback.message.answer(replies.WRONG_WEEKDAY % config.WEEKDAY_MAP_FULL[weekday])
        return
    await state.update_data(new_date=weekday)
    available_time = schedule.available_time_weekday(weekday)
    buttons = [(t.strftime("%H:%M"), Callbacks.CHOOSE_TIME + t.strftime("%H.%M")) for t in available_time]
    keyboard = inline_keyboard(buttons)
    keyboard.adjust(2, repeat=True)
    await callback.message.answer(replies.CHOOSE_TIME, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHOOSE_TIME))
async def frl_update_sl(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `reschedule_lesson_create_reschedule` state."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    state_data = await state.get_data()
    time = datetime.strptime(callback.data.split(":")[1], "%H.%M").time()  # type: ignore  # noqa: PGH003, DTZ007
    user: User = UserRepo(db).get(state_data["user_id"])
    sl: ScheduledLesson = ScheduledLessonRepo(db).get(state_data["lesson"])
    old_w, old_t = sl.weekday_full_str, sl.st_str
    sl.weekday = state_data["new_date"]
    sl.start_time = time
    sl.end_time = time.replace(hour=time.hour + 1) if time.hour < MAX_HOUR else time.replace(hour=0)
    message = replies.USER_MOVED_SL % (
        user.username_dog,
        old_w,
        old_t,
        config.WEEKDAY_MAP_FULL[state_data["new_date"]],
        time.strftime("%H:%M"),
    )
    db.commit()
    await send_message(user.teacher.telegram_id, message)
    await callback.message.answer(replies.LESSON_ADDED)
    await state.clear()

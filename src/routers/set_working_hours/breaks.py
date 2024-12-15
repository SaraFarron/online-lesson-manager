from __future__ import annotations

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from config import config
from errors import AiogramTelegramError, NoTextMessageError, PermissionDeniedError
from messages import replies
from models import Teacher, WorkBreak
from routers.set_working_hours.config import router
from utils import inline_keyboard


class SetWorkingHoursState(StatesGroup):
    add_break = State()


@router.callback_query(F.data == "swh:edit_breaks")
async def edit_breaks(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for editing breaks."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    teacher = db.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
    if teacher is None:
        raise PermissionDeniedError

    breaks = db.query(WorkBreak).filter(WorkBreak.teacher_id == teacher.id).all()
    breaks = [
        (
            f"Убрать: {b.weekday_short_str} {b.st_str}-{b.et_str}",
            f"swh:rm_break_{b.id}",
        )
        for b in breaks
    ]

    keyboard = inline_keyboard([*breaks, ("Добавить перерыв", "swh:add_break")])
    keyboard.adjust(1 if len(breaks) <= config.MAX_BUTTON_ROWS else 2, repeat=True)
    await callback.message.answer(replies.EDIT_BREAKS, reply_markup=keyboard.as_markup())


@router.callback_query(F.data == "swh:add_break")
async def add_break(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for adding breaks."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    teacher = db.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
    if teacher is None:
        raise PermissionDeniedError
    await callback.message.answer(
        replies.CHOOSE_WEEKDAY,
        reply_markup=inline_keyboard(
            [
                (config.WEEKDAY_MAP[d], f"swh:add_break_{d}")
                for d in range(7)
                if d not in [w.weekday for w in teacher.weekends]
            ],
        ).as_markup(),
    )


@router.callback_query(F.data.startswith("swh:add_break_"))
async def add_break_choose_time(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler for finishing adding breaks."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    teacher = db.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
    if teacher is None:
        raise PermissionDeniedError
    await state.update_data(weekday=int(callback.data.replace("swh:add_break_", "")))  # type: ignore  # noqa: PGH003
    await state.set_state(SetWorkingHoursState.add_break)
    await callback.message.answer(replies.CHOOSE_BREAK_PERIOD)


@router.message(SetWorkingHoursState.add_break)
async def add_break_finish(message: Message, state: FSMContext, db: Session) -> None:
    """Handler for finishing adding breaks."""
    if not message.from_user:
        raise AiogramTelegramError
    data = await state.get_data()
    teacher = db.query(Teacher).filter(Teacher.telegram_id == message.from_user.id).first()
    if teacher is None:
        raise PermissionDeniedError
    if not message.text:
        raise NoTextMessageError
    text = message.text.replace(" - ", "-") if " - " in message.text else message.text
    try:
        splitted_text = text.split("-")
        start_time = datetime.strptime(splitted_text[0], "%H:%M").time()  # noqa: DTZ007
        end_time = datetime.strptime(splitted_text[1], "%H:%M").time()  # noqa: DTZ007
    except ValueError:
        await message.answer(replies.INVALID_TIME_PERIOD)
        return
    work_break = WorkBreak(
        teacher_id=teacher.id,
        teacher=teacher,
        weekday=data["weekday"],
        start_time=start_time,
        end_time=end_time,
    )
    db.add(work_break)
    db.commit()

    await message.answer(replies.BREAK_ADDED)
    await state.clear()


@router.callback_query(F.data.startswith("swh:rm_break_"))
async def remove_break(callback: CallbackQuery, state: FSMContext, db: Session) -> None:  # noqa: ARG001
    """Handler for removing breaks."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    teacher = db.query(Teacher).filter(Teacher.telegram_id == callback.from_user.id).first()
    if teacher is None:
        raise PermissionDeniedError
    wb = db.query(WorkBreak).filter(WorkBreak.id == int(callback.data.replace("swh:rm_break_", ""))).first()  # type: ignore  # noqa: PGH003
    if wb:
        db.delete(wb)
    db.commit()

    await callback.message.answer(replies.BREAK_REMOVED)

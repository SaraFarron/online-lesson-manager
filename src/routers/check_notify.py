from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from errors import AiogramTelegramError, PermissionDeniedError
from help import AdminCommands
from messages import replies
from middlewares import DatabaseMiddleware
from models import Reschedule, ScheduledLesson
from repositories import TeacherRepo
from service import Schedule
from utils import inline_keyboard, send_message

router: Router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())


COMMAND = "/check_notify"


class Callbacks:
    CHECK_NOTIFY = "check_notify:"


@router.message(Command(COMMAND))
@router.message(F.text == AdminCommands.CHECK_SCHEDULE.value)
async def check_notify_handler(message: Message, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `/check_notify` command."""
    if message.from_user is None:
        raise AiogramTelegramError
    teacher = TeacherRepo(db).get_by_telegram_id(message.from_user.id)
    if teacher is None:
        raise PermissionDeniedError

    schedule = Schedule(db)
    collisions = schedule.check_schedule_consistency(teacher)
    wrong_time_events = collisions.all_collisions
    text = collisions.message

    await state.update_data(wrong_time_events=wrong_time_events)

    keyboard = inline_keyboard(
        [
            ("Отправить сообщения", Callbacks.CHECK_NOTIFY + "send"),
            ("Отмена", Callbacks.CHECK_NOTIFY + "cancel"),
        ],
    )
    await message.answer(text, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith(Callbacks.CHECK_NOTIFY))
async def check_notify_finish(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    """Handler receives messages with `check_notify` callback."""
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError
    message = callback.message
    if callback.data == Callbacks.CHECK_NOTIFY + "send":
        err_msg = "Ошибка при отправке уведомления "
        state_data = await state.get_data()
        for we in state_data["wrong_time_events"]:
            if isinstance(we, ScheduledLesson):
                lesson: Reschedule | ScheduledLesson | None = db.query(ScheduledLesson).get(we.id)
                if not lesson:
                    await message.answer(err_msg + f"об уроке {we.weekday_full_str}-{we.start_time}")
                    continue
            elif isinstance(we, Reschedule):
                lesson: Reschedule | ScheduledLesson | None = db.query(Reschedule).get(we.id)
                if not lesson:
                    await message.answer(err_msg + f"о переносе {we.date} в {we.st_str}")
                    continue
            else:
                continue
            await send_message(lesson.user.telegram_id, replies.OUT_OF_WT % str(lesson))
        await message.answer("Уведомления отправлены")
    else:
        await state.clear()
        await message.answer("Отменено")

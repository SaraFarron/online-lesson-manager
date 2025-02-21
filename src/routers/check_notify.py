from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session
from service import Service
from errors import AiogramTelegramError
from help import AdminCommands
from middlewares import DatabaseMiddleware
from utils import inline_keyboard

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

    service = Service(db)
    teacher = service.get_teacher(message.from_user.id)

    wrong_time_events, text = service.check_schedule_consistency(teacher)

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
    message = callback.message
    if not isinstance(callback.message, Message):
        raise AiogramTelegramError

    if callback.data == Callbacks.CHECK_NOTIFY + "send":
        state_data = await state.get_data()
        service = Service(db)
        service.send_messages(state_data["wrong_time_events"])
        await message.answer("Уведомления отправлены")
    else:
        await state.clear()
        await message.answer("Отменено")

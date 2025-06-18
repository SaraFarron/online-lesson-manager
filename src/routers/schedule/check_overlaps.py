
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.db.models import User
from src.db.schemas import RolesSchema
from src.keyboards import AdminCommands, Keyboards
from src.messages import replies
from src.middlewares import DatabaseMiddleware
from src.services import EventRepo, UserService
from src.utils import send_message, telegram_checks

router = Router()
router.message.middleware(DatabaseMiddleware())
router.callback_query.middleware(DatabaseMiddleware())

class CheckOverlaps(StatesGroup):
    scene = "check_overlaps"
    command = "/" + scene
    base_callback = scene + "/"
    send_messages = f"{base_callback}send_messages"


@router.message(Command(CheckOverlaps.command))
@router.message(F.text == AdminCommands.CHECK_OVERLAPS.value)
async def check_overlaps_handler(message: Message, state: FSMContext, db: Session) -> None:
    message, user = UserService(db).check_user(message, RolesSchema.TEACHER)

    await state.update_data(user_id=message.from_user.id)
    overlaps = EventRepo(db).overlaps(user.executor_id)
    if overlaps:
        texts = EventRepo(db).overlaps_text(overlaps)
        if texts:
            await message.answer(
                "Замечены несостыковки\n" + "\n".join(texts),
                reply_markup=Keyboards.send_messages(CheckOverlaps.send_messages),
            )
        else:
            await message.answer(replies.NO_OVERLAPS)
    else:
        await message.answer(replies.NO_OVERLAPS)


@router.callback_query(F.data.startswith(CheckOverlaps.send_messages))
async def send_messages(callback: CallbackQuery, state: FSMContext, db: Session) -> None:
    message = telegram_checks(callback)
    state_data = await state.get_data()
    user = UserService(db).get_by_telegram_id(state_data["user_id"], True)
    if user.role != User.Roles.TEACHER:
        raise Exception("message", replies.PERMISSION_DENIED, "user.role != Teacher")

    overlaps = EventRepo(db).overlaps(user.executor_id)
    messages = EventRepo(db).overlaps_messages(overlaps)
    counter = 0
    for user_tg, texts in messages.items():
        if not texts:
            continue
        msg = "В вашем расписании занятий есть несостыковки:\n" + "\n".join(texts)
        await send_message(user_tg, msg)
        counter += 1

    await message.answer(f"sent messages to {counter} users")
    await state.clear()

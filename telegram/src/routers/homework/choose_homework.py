
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.orm import Session

from src.keyboards import Commands, Keyboards
from src.messages import replies
from src.service.services import HomeWorkService, UserService
from src.service.utils import get_callback_arg

router = Router()


router.callback_query.middleware(DatabaseMiddleware())


class ChooseHomework(StatesGroup):
    scene = "choose_homework"
    command = "/" + scene
    base_callback = scene + "/"
    homework_list = f"{base_callback}list/"
    action = f"{base_callback}action/"


@router.message(Command(ChooseHomework.command))
@router.message(F.text == Commands.CHOOSE_HOMEWORK.value)
async def choose_hw_handler(message: Message, state: FSMContext, db: Session) -> None:
    message, user = UserService(db).check_user(message)

    await state.update_data(user_id=user.telegram_id)
    homeworks = HomeWorkService(db).homeworks(user)
    await message.answer(
        replies.CHOOSE_HOMEWORK,
        reply_markup=Keyboards.homeworks(homeworks, ChooseHomework.homework_list),
    )


@router.callback_query(F.data.startswith(ChooseHomework.homework_list))
async def choose_action(callback: CallbackQuery, state: FSMContext, db: Session):
    state_data = await state.get_data()
    message, _ = UserService(db).check_user_with_id(callback, state_data["user_id"])

    hw_id = int(get_callback_arg(callback.data, ChooseHomework.homework_list))
    await state.update_data(hw_id=hw_id)

    await message.answer(replies.CHOOSE_ACTION, reply_markup=Keyboards.hw_actions(ChooseHomework.action))

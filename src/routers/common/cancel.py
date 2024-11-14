from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from help import Commands
from logger import log_func

COMMAND = "cancel"

router: Router = Router()


class Messages:
    CANCELED = "Отмена"


@router.message(Command(COMMAND))
@router.message(F.text == Commands.CANCEL.value)
@log_func
async def cancel_handler(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `/cancel` command."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer(Messages.CANCELED)
        return

    await state.clear()
    await message.answer(Messages.CANCELED)  # , reply_markup=ReplyKeyboardRemove()

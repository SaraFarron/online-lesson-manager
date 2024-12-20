from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from messages import replies

COMMAND = "cancel"

router: Router = Router()


@router.message(Command(COMMAND))
async def cancel_handler(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `/cancel` command."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer(replies.CANCEL)
        return

    await state.clear()
    await message.answer(replies.CANCEL)  # , reply_markup=ReplyKeyboardRemove()

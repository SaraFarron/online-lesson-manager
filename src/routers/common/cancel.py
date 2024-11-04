from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from log import log_func

router: Router = Router()


@router.message(Command("cancel"))
@log_func
async def cancel_handler(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `/cancel` command."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Canceled")
        return

    await state.clear()
    await message.answer("Canceled")  # , reply_markup=ReplyKeyboardRemove()

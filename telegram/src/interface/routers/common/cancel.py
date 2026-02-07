from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.interface.messages import replies

router: Router = Router()


@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext) -> None:
    """Handler receives messages with `/cancel` command."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer(replies.CANCEL)
        return

    await state.clear()
    await message.answer(replies.CANCEL)

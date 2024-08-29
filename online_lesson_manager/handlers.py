from aiogram import Router, html
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from online_lesson_manager.keyborads import builder

router: Router = Router()


@router.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    """Handler receives messages with `/start` command."""
    await message.answer(f"你好, {html.bold(message.from_user.full_name)}!")


@router.message(Command("get_schedule"))
async def get_schedule(message: Message) -> None:
    """Handler receives messages with `/schedule` command."""
    await message.answer("Calendar", reply_markup=builder.as_markup())

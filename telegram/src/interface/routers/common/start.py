from aiogram import Router, html
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message

from src.interface.messages import replies
from src.service import StartService
from src.service.utils import telegram_checks

router: Router = Router()


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def start_handler(message: Message, command: CommandObject) -> None:
    """Handler receives messages with `/start` command."""
    message = telegram_checks(message)
    service = StartService(message)
    user = await service.get_user()
    if user is None:
        # code = command.args
        code = "abc"
        user = await service.register(code)

    if user is None:
        await message.answer(replies.REGISTRATION_FAILED)
        return
    await message.answer(replies.GREETINGS % html.bold(user.full_name))
    await message.answer(replies.BOT_DESCRIPTION)

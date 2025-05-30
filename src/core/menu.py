from aiogram.types.bot_command import BotCommand

ALL_COMMANDS = [
    BotCommand(command="start", description="Приветственное сообщение, запуск бота"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="cancel", description="Отмена"),
]

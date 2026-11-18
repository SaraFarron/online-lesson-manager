import logging
from collections.abc import Awaitable, Callable
from html import escape
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import (
    Command,
    CommandObject,
    CommandStart,
    ExceptionTypeFilter,
)
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    CallbackQuery,
    ErrorEvent,
    Message,
    TelegramObject,
)
from aiogram_dialog import DialogManager, ShowMode, StartMode, setup_dialogs
from aiogram_dialog.api.exceptions import OutdatedIntent, UnknownIntent

from telegram.api import (
    BackendClient,
    BackendContractError,
    BackendError,
    BackendUnavailable,
    SessionView,
)
from telegram.dialogs.breaks import build_break_dialog
from telegram.dialogs.broadcast import build_broadcast_dialog
from telegram.dialogs.home import Screens, build_dialog
from telegram.dialogs.homework.student import build_homework_student_dialog
from telegram.dialogs.homework.teacher_creation import build_homework_teacher_dialog
from telegram.dialogs.homework.teacher_management import (
    build_homework_teacher_management_dialog,
)
from telegram.dialogs.lessons.creation import build_lesson_dialog
from telegram.dialogs.lessons.management import build_event_management_dialog
from telegram.dialogs.onboarding import Wizard, build_wizard
from telegram.dialogs.schedule.dialog import build_schedule_dialog
from telegram.dialogs.settings import build_settings_dialog, start_settings
from telegram.dialogs.student_profiles import build_student_profiles_dialog
from telegram.dialogs.vacations import build_vacation_dialog, start_vacation_management
from telegram.invitations import create_teacher_invitation, parse_teacher_start_payload
from telegram.shared.command_cleanup import (
    delete_command_message,
    is_private_command,
    remove_stale_reply_keyboard,
)

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="start", description="Open the main menu"),
    BotCommand(command="settings", description="Account and notification settings"),
    BotCommand(command="vacations", description="Manage vacations"),
]


async def publish_commands(bot: Bot) -> None:
    await bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllPrivateChats())


class EditInputMiddleware(BaseMiddleware):
    async def __call__(
        self, handler: Any, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        manager = data.get("dialog_manager")
        if isinstance(event, Message) and manager is not None:
            manager.show_mode = ShowMode.EDIT
        return await handler(event, data)


class CommandCleanupMiddleware(BaseMiddleware):
    async def __call__(
        self, handler: Any, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        if isinstance(event, Message) and is_private_command(event):
            await delete_command_message(event)
        return await handler(event, data)


async def start(
    message: Message, dialog_manager: DialogManager, command: CommandObject
) -> None:
    await remove_stale_reply_keyboard(message)
    await dialog_manager.start(
        Screens.welcome, mode=StartMode.RESET_STACK, show_mode=ShowMode.EDIT
    )
    client = dialog_manager.middleware_data.get("backend_client")
    if client is None or message.from_user is None:
        return
    try:
        view = await client.login(
            message.from_user.id,
            message.chat.id,
            telegram_username=message.from_user.username,
            sync_username=True,
        )
    except BackendUnavailable as exc:
        identity = client.offline_schedule_identity(message.from_user.id)
        if identity is not None:
            dialog_manager.dialog_data.update(
                offline_schedule=True,
                offline_sender_id=message.from_user.id,
                error="",
            )
            await dialog_manager.switch_to(
                Screens.offline_schedule, show_mode=ShowMode.EDIT
            )
            await dialog_manager.show(show_mode=ShowMode.EDIT)
            return
        dialog_manager.dialog_data["error"] = str(exc)
        await dialog_manager.switch_to(Screens.unavailable, show_mode=ShowMode.EDIT)
        await dialog_manager.show(show_mode=ShowMode.EDIT)
        return
    except (BackendContractError, BackendError) as exc:
        dialog_manager.dialog_data["error"] = str(exc)
        await dialog_manager.switch_to(Screens.unavailable, show_mode=ShowMode.EDIT)
        await dialog_manager.show(show_mode=ShowMode.EDIT)
        return
    if view is not None:
        dialog_manager.dialog_data["view"] = view.model_dump(mode="json")
        dialog_manager.dialog_data["existing_invitation"] = bool(command.args)
        if view.teacher is not None:
            dialog_manager.dialog_data["invitation"] = await create_teacher_invitation(
                dialog_manager.middleware_data["bot"], view.teacher.code
            )
        await dialog_manager.switch_to(Screens.home, show_mode=ShowMode.EDIT)
        await dialog_manager.show(show_mode=ShowMode.EDIT)
    elif command.args:
        teacher_code = parse_teacher_start_payload(command.args)
        await dialog_manager.start(
            Wizard.code,
            data={
                "role": "STUDENT",
                "teacher_code": teacher_code,
                "invalid_invitation": teacher_code is None,
            },
            show_mode=ShowMode.EDIT,
        )


async def _open_from_command(
    message: Message,
    dialog_manager: DialogManager,
    starter: Callable[[SessionView, DialogManager], Awaitable[None]],
    feature: str,
) -> None:
    client = dialog_manager.middleware_data.get("backend_client")
    sender = message.from_user
    if client is None or sender is None:
        return
    try:
        view = await client.view(sender.id, message.chat.id)
    except BackendError as exc:
        if exc.status in {401, 403, 404}:
            await message.answer(f"{feature} are unavailable. Send /start to register.")
        else:
            await message.answer(f"{escape(str(exc))}\nSend /start to retry.")
        return
    except (BackendUnavailable, BackendContractError) as exc:
        await message.answer(f"{escape(str(exc))}\nSend /start to retry.")
        return
    if view.account.role not in {"TEACHER", "STUDENT"}:
        await message.answer(f"{feature} are available to teachers and students.")
        return
    await starter(view, dialog_manager)


async def settings_command(message: Message, dialog_manager: DialogManager) -> None:
    await _open_from_command(message, dialog_manager, start_settings, "Settings")


async def vacations_command(message: Message, dialog_manager: DialogManager) -> None:
    await _open_from_command(
        message, dialog_manager, start_vacation_management, "Vacations"
    )


async def stale_callback(event: ErrorEvent) -> bool:
    callback = event.update.callback_query
    if callback is not None:
        await callback.answer(
            "This screen expired. Send /start to continue.", show_alert=True
        )
    return True


async def orphan_callback(callback: CallbackQuery) -> None:
    await callback.answer(
        "This screen expired. Send /start to continue.", show_alert=True
    )


def build_dispatcher(client: BackendClient | None = None) -> Dispatcher:
    isolation = SimpleEventIsolation()
    dispatcher = Dispatcher(storage=MemoryStorage(), events_isolation=isolation)
    if client is not None:
        dispatcher["backend_client"] = client
    commands = Router(name="commands")
    commands.message.register(start, CommandStart(), F.chat.type == "private")
    commands.message.register(
        settings_command, Command("settings"), F.chat.type == "private"
    )
    commands.message.register(
        vacations_command, Command("vacations"), F.chat.type == "private"
    )
    dispatcher.include_router(commands)
    dispatcher.include_router(build_dialog())
    dispatcher.include_router(build_broadcast_dialog())
    dispatcher.include_router(build_schedule_dialog())
    dispatcher.include_router(build_lesson_dialog())
    dispatcher.include_router(build_event_management_dialog())
    dispatcher.include_router(build_break_dialog())
    dispatcher.include_router(build_vacation_dialog())
    dispatcher.include_router(build_settings_dialog())
    dispatcher.include_router(build_homework_teacher_dialog())
    dispatcher.include_router(build_homework_student_dialog())
    dispatcher.include_router(build_homework_teacher_management_dialog())
    dispatcher.include_router(build_student_profiles_dialog())
    dispatcher.include_router(build_wizard())
    fallback = Router(name="stale-callbacks")
    fallback.callback_query.register(orphan_callback)
    dispatcher.include_router(fallback)
    dispatcher.message.filter(F.chat.type == "private")
    dispatcher.callback_query.filter(F.message.chat.type == "private")
    dispatcher.errors.register(
        stale_callback, ExceptionTypeFilter(UnknownIntent, OutdatedIntent)
    )
    setup_dialogs(dispatcher)
    dispatcher.message.middleware(EditInputMiddleware())
    dispatcher.message.outer_middleware(CommandCleanupMiddleware())
    return dispatcher

import logging
from typing import Any

from aiogram import BaseMiddleware, Dispatcher, F, Router
from aiogram.filters import CommandObject, CommandStart, ExceptionTypeFilter
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import CallbackQuery, ErrorEvent, Message, TelegramObject
from aiogram_dialog import DialogManager, ShowMode, StartMode, setup_dialogs
from aiogram_dialog.api.exceptions import OutdatedIntent, UnknownIntent

from telegram.api import BackendClient, BackendError, BackendUnavailable
from telegram.break_dialogs import build_break_dialog
from telegram.dialogs import Screens, build_dialog
from telegram.event_management_dialogs import build_event_management_dialog
from telegram.invitations import create_teacher_invitation, parse_teacher_start_payload
from telegram.lesson_dialogs import build_lesson_dialog
from telegram.onboarding import Wizard, build_wizard
from telegram.schedule_dialogs import build_schedule_dialog
from telegram.vacation_dialogs import build_vacation_dialog

logger = logging.getLogger(__name__)


class EditInputMiddleware(BaseMiddleware):
    async def __call__(
        self, handler: Any, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        manager = data.get("dialog_manager")
        if isinstance(event, Message) and manager is not None:
            manager.show_mode = ShowMode.EDIT
        return await handler(event, data)


async def start(
    message: Message, dialog_manager: DialogManager, command: CommandObject
) -> None:
    await dialog_manager.start(
        Screens.welcome, mode=StartMode.RESET_STACK, show_mode=ShowMode.EDIT
    )
    client = dialog_manager.middleware_data.get("backend_client")
    if client is None or message.from_user is None:
        return
    try:
        view = await client.login(message.from_user.id, message.chat.id)
    except (BackendError, BackendUnavailable) as exc:
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
    dispatcher.include_router(commands)
    dispatcher.include_router(build_dialog())
    dispatcher.include_router(build_schedule_dialog())
    dispatcher.include_router(build_lesson_dialog())
    dispatcher.include_router(build_event_management_dialog())
    dispatcher.include_router(build_break_dialog())
    dispatcher.include_router(build_vacation_dialog())
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
    return dispatcher

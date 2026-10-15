from html import escape
from typing import Any

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, ShowMode, Window
from aiogram_dialog.widgets.input import ManagedTextInput, TextInput
from aiogram_dialog.widgets.kbd import Button, SwitchTo
from aiogram_dialog.widgets.text import Const, Format

from telegram.api import SessionView
from telegram.inputs import digest_clock, parse_timezone
from telegram.onboarding import begin_student, begin_teacher


class Screens(StatesGroup):
    welcome = State()
    details = State()
    timezone = State()
    home = State()
    unavailable = State()


async def screen_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    raw_view = dialog_manager.dialog_data.get("view")
    home = ""
    if raw_view is not None:
        view = SessionView.model_validate(raw_view)
        account = view.account
        home = (
            f"{account.role}: {account.full_name or 'Registered account'}\n"
            f"Timezone: {account.timezone}\n"
            f"Daily digest (local): {digest_clock(account.notification_lessons_today)}\n"
        )
        if dialog_manager.dialog_data.get("existing_invitation"):
            home += "\nAlready registered. This invitation did not change your role or teacher."
        if view.teacher is not None:
            profile = view.teacher
            home += (
                f"Teacher code: {profile.code}\n"
                f"Work hours: {profile.work_start}–{profile.work_end}\n"
                f"ISO weekdays: {profile.working_days}\n"
                f"Lesson/break minutes: {profile.lesson_length}/{profile.between_lessons_break}\n"
                f"Consecutive/daily limits: {profile.max_consecutive_lessons}/{profile.max_lessons_per_day}\n"
            )
        if view.student is not None:
            lesson = (
                "off"
                if view.student.notification_lesson is None
                else f"{view.student.notification_lesson} minutes"
            )
            homework = (
                "off"
                if view.student.notification_homework is None
                else f"{view.student.notification_homework} days"
            )
            home += f"Lesson reminder: {lesson}\nHomework reminder: {homework}\n"
        home += (
            "Preferences saved. Notifications and schedule rules are not active yet."
        )
        invitation = dialog_manager.dialog_data.get("invitation", "")
        if invitation:
            home += f"\nStudent invitation: {invitation}"
    return {
        "timezone": escape(dialog_manager.dialog_data.get("timezone", "not confirmed")),
        "error": escape(dialog_manager.dialog_data.get("error", "")),
        "home": escape(home),
        "proof": "backend_client" not in dialog_manager.middleware_data,
    }


async def timezone_input(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    manager.dialog_data.update(timezone=value, error="")
    await manager.switch_to(Screens.details, show_mode=ShowMode.EDIT)


async def input_error(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    error: ValueError,
) -> None:
    manager.dialog_data["error"] = str(error)
    manager.show_mode = ShowMode.EDIT


async def confirm_utc(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(timezone="UTC", error="")
    await manager.switch_to(Screens.details, show_mode=ShowMode.EDIT)


async def wizard_result(_start_data: Any, result: Any, manager: DialogManager) -> None:
    if result is not None:
        manager.dialog_data.update(result)
        await manager.switch_to(Screens.home, show_mode=ShowMode.EDIT)


def build_dialog() -> Dialog:
    return Dialog(
        Window(
            Const(
                "Online Lesson Manager\nRegistration settings are saved in the backend."
            ),
            Button(Const("Register as teacher"), id="teacher", on_click=begin_teacher),
            Button(Const("Register as student"), id="student", on_click=begin_student),
            SwitchTo(
                Const("Continue"), id="continue", state=Screens.details, when="proof"
            ),
            state=Screens.welcome,
        ),
        Window(
            Format("Timezone: {timezone}\nConfirm your timezone before registration."),
            SwitchTo(Const("Timezone"), id="timezone", state=Screens.timezone),
            SwitchTo(Const("Back"), id="back", state=Screens.welcome),
            state=Screens.details,
        ),
        Window(
            Format("Enter an IANA timezone, for example Asia/Bangkok.\n{error}"),
            TextInput(
                "timezone_input",
                type_factory=parse_timezone,
                on_success=timezone_input,
                on_error=input_error,
            ),
            Button(Const("Use UTC"), id="utc", on_click=confirm_utc),
            SwitchTo(Const("Back"), id="back", state=Screens.details),
            state=Screens.timezone,
        ),
        Window(Format("{home}"), state=Screens.home),
        Window(
            Format("{error}\nSend /start to retry. No offline account is created."),
            state=Screens.unavailable,
        ),
        getter=screen_data,
        on_process_result=wizard_result,
    )

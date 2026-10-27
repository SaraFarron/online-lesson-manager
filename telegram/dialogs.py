from html import escape
from typing import Any

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, ShowMode, Window
from aiogram_dialog.widgets.input import ManagedTextInput, TextInput
from aiogram_dialog.widgets.kbd import Button, Row, SwitchTo
from aiogram_dialog.widgets.text import Const, Format

from telegram.api import SessionView
from telegram.break_dialogs import open_break_management
from telegram.event_management_dialogs import open_event_management
from telegram.homework_student_dialogs import open_student_homework
from telegram.homework_teacher_dialogs import open_homework_creation
from telegram.homework_teacher_management_dialogs import (
    open_teacher_homework_management,
)
from telegram.inputs import digest_clock, format_working_days, parse_timezone
from telegram.lesson_dialogs import open_lesson_creation
from telegram.onboarding import begin_student, begin_teacher
from telegram.schedule_dialogs import open_today, open_week
from telegram.settings_dialogs import open_settings
from telegram.vacation_dialogs import open_vacation_management


class Screens(StatesGroup):
    welcome = State()
    details = State()
    timezone = State()
    home = State()
    unavailable = State()


async def screen_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    raw_view = dialog_manager.dialog_data.get("view")
    view = SessionView.model_validate(raw_view) if raw_view is not None else None
    home = ""
    if view is not None:
        account = view.account
        home = (
            f"{account.role}: {account.full_name or 'Registered account'}\n"
            f"Timezone: {account.timezone}\n"
            f"Daily digest (local): {digest_clock(account.notification_lessons_today)}\n"
        )
        if dialog_manager.dialog_data.get("homework_notice"):
            home += f"\n{dialog_manager.dialog_data['homework_notice']}"
        if dialog_manager.dialog_data.get("existing_invitation"):
            home += "\nAlready registered. This invitation did not change your role or teacher."
        if view.teacher is not None:
            profile = view.teacher
            home += (
                f"Teacher code: {profile.code}\n"
                f"Work hours: {profile.work_start}–{profile.work_end}\n"
                f"Workdays: {format_working_days(profile.working_days)}\n"
                f"Lesson/break minutes: {profile.lesson_length}/{profile.between_lessons_break}\n"
                f"Consecutive/daily limits: {profile.max_consecutive_lessons}/{profile.max_lessons_per_day}\n"
            )
        elif account.role == "TEACHER":
            home += "Teacher profile unavailable.\n"
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
        elif account.role == "STUDENT":
            home += "Student profile unavailable.\n"
        if account.role == "STUDENT":
            home += (
                "Preferences saved. Lesson booking and schedule viewing are available. "
                "Existing events can be managed. "
                "Notifications are not active yet."
            )
        elif account.role == "ADMIN":
            home += (
                "Preferences saved. Booking rules and notifications are not active yet."
            )
        invitation = dialog_manager.dialog_data.get("invitation", "")
        if invitation:
            home += f"\nStudent invitation: {invitation}"
    return {
        "timezone": escape(dialog_manager.dialog_data.get("timezone", "not confirmed")),
        "error": escape(dialog_manager.dialog_data.get("error", "")),
        "home": escape(home),
        "proof": "backend_client" not in dialog_manager.middleware_data,
        "can_view_schedule": view is not None
        and view.account.role in {"TEACHER", "STUDENT"},
        "can_book_lessons": view is not None
        and view.account.role in {"TEACHER", "STUDENT"},
        "can_manage_events": view is not None
        and view.account.role in {"TEACHER", "STUDENT"},
        "can_manage_breaks": view is not None and view.account.role == "TEACHER",
        "can_manage_vacations": view is not None
        and view.account.role in {"TEACHER", "STUDENT"},
        "can_edit_settings": view is not None
        and view.account.role in {"TEACHER", "STUDENT"},
        "can_manage_homework": view is not None and view.account.role == "TEACHER",
        "can_view_homework": view is not None and view.account.role == "STUDENT",
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
        Window(
            Format("{home}"),
            Button(
                Const("Add lesson"),
                id="add_lesson",
                on_click=open_lesson_creation,
                when="can_book_lessons",
            ),
            Button(
                Const("Manage events"),
                id="manage_events",
                on_click=open_event_management,
                when="can_manage_events",
            ),
            Button(
                Const("Homework"),
                id="homework",
                on_click=open_homework_creation,
                when="can_manage_homework",
            ),
            Button(
                Const("Manage homework"),
                id="manage_homework",
                on_click=open_teacher_homework_management,
                when="can_manage_homework",
            ),
            Button(
                Const("Homework"),
                id="student_homework",
                on_click=open_student_homework,
                when="can_view_homework",
            ),
            Button(
                Const("Breaks"),
                id="manage_breaks",
                on_click=open_break_management,
                when="can_manage_breaks",
            ),
            Button(
                Const("Vacations"),
                id="manage_vacations",
                on_click=open_vacation_management,
                when="can_manage_vacations",
            ),
            Button(
                Const("Settings"),
                id="settings",
                on_click=open_settings,
                when="can_edit_settings",
            ),
            Row(
                Button(
                    Const("Today"),
                    id="schedule_today",
                    on_click=open_today,
                    when="can_view_schedule",
                ),
                Button(
                    Const("This week"),
                    id="schedule_week",
                    on_click=open_week,
                    when="can_view_schedule",
                ),
            ),
            state=Screens.home,
        ),
        Window(
            Format("{error}\nSend /start to retry. No offline account is created."),
            state=Screens.unavailable,
        ),
        getter=screen_data,
        on_process_result=wizard_result,
    )

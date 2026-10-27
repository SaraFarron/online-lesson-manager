from html import escape
from typing import Any

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, ShowMode, StartMode, Window
from aiogram_dialog.widgets.input import ManagedTextInput, TextInput
from aiogram_dialog.widgets.kbd import Button, Multiselect, Row
from aiogram_dialog.widgets.kbd.select import ManagedMultiselect
from aiogram_dialog.widgets.text import Const, Format
from pydantic import ValidationError

from telegram.api import (
    BackendClient,
    BackendError,
    BackendOutcomeUnknown,
    BackendUnavailable,
    SelfProfileUpdate,
    SessionView,
    StudentProfileUpdate,
    TeacherProfileUpdate,
)
from telegram.feedback import feedback_block
from telegram.inputs import (
    DAY_NAMES,
    digest_clock,
    parse_clock,
    parse_integer,
    parse_timezone,
)

ACCOUNT_FIELDS = ("timezone", "notification_lessons_today")
TEACHER_FIELDS = (
    "work_start",
    "work_end",
    "lesson_length",
    "between_lessons_break",
    "max_consecutive_lessons",
    "max_lessons_per_day",
)
TEACHER_FIELD_LABELS = {
    "work_start": "Work starts",
    "work_end": "Work ends",
    "lesson_length": "Lesson length",
    "between_lessons_break": "Between-lesson break",
    "max_consecutive_lessons": "Consecutive lesson limit",
    "max_lessons_per_day": "Lessons per day",
}


class SettingsScreens(StatesGroup):
    menu = State()
    account = State()
    teacher = State()
    student = State()
    field = State()
    days = State()
    reminder_unit = State()
    unavailable = State()


def _backend(manager: DialogManager) -> BackendClient:
    client = manager.middleware_data["backend_client"]
    if not isinstance(client, BackendClient):
        raise TypeError("Backend client is not configured")
    return client


def _feedback(data: dict[str, Any]) -> str:
    return feedback_block(
        error=str(data.get("error", "")),
        warning=str(data.get("warning", "")),
    )


def _settings_data(view: SessionView) -> dict[str, Any]:
    if view.account.role not in {"TEACHER", "STUDENT"}:
        raise ValueError("This account cannot edit Telegram settings")
    account = {
        "timezone": view.account.timezone,
        "notification_lessons_today": view.account.notification_lessons_today,
    }
    data: dict[str, Any] = {
        "view": view.model_dump(mode="json"),
        "role": view.account.role,
        "account_original": account.copy(),
        "account_draft": account.copy(),
        "teacher_original": None,
        "teacher_draft": None,
        "student_original": None,
        "student_draft": None,
        "section": None,
        "field_key": None,
        "reminder_unit": None,
        "error": "",
        "warning": "",
        "notice": "",
        "needs_reload": False,
        "saving": False,
    }
    if view.account.role == "TEACHER":
        if view.teacher is None:
            raise ValueError("Teacher profile is unavailable")
        working_days = list(view.teacher.working_days)
        profile = {
            "work_start": view.teacher.work_start,
            "work_end": view.teacher.work_end,
            "working_days": working_days,
            "lesson_length": view.teacher.lesson_length,
            "between_lessons_break": view.teacher.between_lessons_break,
            "max_consecutive_lessons": view.teacher.max_consecutive_lessons,
            "max_lessons_per_day": view.teacher.max_lessons_per_day,
        }
        data["teacher_original"] = profile.copy()
        data["teacher_draft"] = {**profile, "working_days": working_days[:]}
    else:
        if view.student is None:
            raise ValueError("Student profile is unavailable")
        profile = {
            "notification_lesson": view.student.notification_lesson,
            "notification_homework": view.student.notification_homework,
        }
        data["student_original"] = profile.copy()
        data["student_draft"] = profile.copy()
    return data


def _settings_view(manager: DialogManager) -> SessionView:
    return SessionView.model_validate(manager.dialog_data["view"])


def _changed(original: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in draft.items() if original.get(key) != value}


async def open_settings(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Settings require a private chat")
    try:
        view = await _backend(manager).view(
            callback.from_user.id, callback.message.chat.id
        )
        data = _settings_data(view)
        state = SettingsScreens.menu
    except (BackendError, BackendUnavailable, ValueError) as exc:
        data = {
            "error": str(exc),
            "warning": "",
            "notice": "",
            "needs_reload": True,
            "saving": False,
        }
        state = SettingsScreens.unavailable
    await manager.start(
        state,
        data=data,
        mode=StartMode.NORMAL,
        show_mode=ShowMode.EDIT,
    )


async def settings_started(start_data: Any, manager: DialogManager) -> None:
    manager.dialog_data.clear()
    manager.dialog_data.update(start_data)


def _section_state(section: str) -> State:
    if section == "account":
        return SettingsScreens.account
    if section == "teacher":
        return SettingsScreens.teacher
    if section == "student":
        return SettingsScreens.student
    raise ValueError("Settings section is missing")


async def open_account(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(section="account", error="", notice="")
    await manager.switch_to(SettingsScreens.account, show_mode=ShowMode.EDIT)


async def open_role_settings(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    role = manager.dialog_data["role"]
    section = "teacher" if role == "TEACHER" else "student"
    manager.dialog_data.update(section=section, error="", notice="")
    await manager.switch_to(_section_state(section), show_mode=ShowMode.EDIT)


async def back_to_menu(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(error="")
    await manager.switch_to(SettingsScreens.menu, show_mode=ShowMode.EDIT)


async def cancel_section(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    section = data["section"]
    if section == "account":
        data["account_draft"] = data["account_original"].copy()
    elif section == "teacher":
        original = data["teacher_original"].copy()
        original["working_days"] = original["working_days"][:]
        data["teacher_draft"] = original
    elif section == "student":
        data["student_draft"] = data["student_original"].copy()
    data.update(error="", notice="Unsaved changes discarded.")
    await manager.switch_to(SettingsScreens.menu, show_mode=ShowMode.EDIT)


async def return_home(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    if data.get("needs_reload") or not data.get("view"):
        await manager.done(show_mode=ShowMode.EDIT)
    else:
        await manager.done(
            result={"view": data["view"]},
            show_mode=ShowMode.EDIT,
        )


async def back_to_section(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.switch_to(
        _section_state(manager.dialog_data["section"]), show_mode=ShowMode.EDIT
    )


async def edit_field(
    _callback: CallbackQuery, button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(
        field_key=button.widget_id,
        error="",
        notice="",
    )
    if button.widget_id == "notification_lesson":
        await manager.switch_to(SettingsScreens.reminder_unit, show_mode=ShowMode.EDIT)
    else:
        await manager.switch_to(SettingsScreens.field, show_mode=ShowMode.EDIT)


async def disable_digest(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["account_draft"]["notification_lessons_today"] = None
    manager.dialog_data.update(error="", notice="")
    await manager.switch_to(SettingsScreens.account, show_mode=ShowMode.EDIT)


async def disable_lesson_reminder(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["student_draft"]["notification_lesson"] = None
    manager.dialog_data.update(error="", notice="")
    await manager.switch_to(SettingsScreens.student, show_mode=ShowMode.EDIT)


async def disable_homework_reminder(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["student_draft"]["notification_homework"] = None
    manager.dialog_data.update(error="", notice="")
    await manager.switch_to(SettingsScreens.student, show_mode=ShowMode.EDIT)


async def choose_reminder_unit(
    _callback: CallbackQuery, button: Button, manager: DialogManager
) -> None:
    unit = button.widget_id
    if unit not in {"hours", "minutes"}:
        raise ValueError("Choose hours or minutes explicitly")
    manager.dialog_data.update(
        reminder_unit=unit,
        field_key="notification_lesson",
        section="student",
        error="",
    )
    await manager.switch_to(SettingsScreens.field, show_mode=ShowMode.EDIT)


def _field_prompt(manager: DialogManager) -> tuple[str, str]:
    key = manager.dialog_data.get("field_key")
    section = manager.dialog_data.get("section")
    prompts = {
        "timezone": (
            "Timezone",
            "Enter a valid IANA timezone, for example Asia/Bangkok.",
        ),
        "notification_lessons_today": (
            "Daily digest time",
            "Enter local time as HH:MM, HH MM or HH/MM.",
        ),
        "work_start": ("Work starts", "Enter a time as HH:MM, HH MM or HH/MM."),
        "work_end": ("Work ends", "Enter a time as HH:MM, HH MM or HH/MM."),
        "lesson_length": ("Lesson length", "Enter positive minutes."),
        "between_lessons_break": (
            "Between-lesson break",
            "Enter minutes; 0 is allowed. This applies to new lessons only; "
            "existing lessons and series keep their saved break duration.",
        ),
        "max_consecutive_lessons": (
            "Consecutive lesson limit",
            "Enter a positive number of lessons.",
        ),
        "max_lessons_per_day": ("Lessons per day", "Enter a positive lesson count."),
        "notification_lesson": (
            "Lesson reminder",
            f"Enter a positive number of {manager.dialog_data.get('reminder_unit')}.",
        ),
        "notification_homework": (
            "Homework reminder",
            "Enter a positive number of days.",
        ),
    }
    if key not in prompts:
        raise ValueError("Settings field is missing")
    if section not in {"account", "teacher", "student"}:
        raise ValueError("Settings section is missing")
    return prompts[key]


def _current_field_value(data: dict[str, Any]) -> Any:
    key = data.get("field_key")
    section = data.get("section")
    if section == "account":
        return data["account_draft"].get(key)
    if section == "teacher":
        return data["teacher_draft"].get(key)
    if section == "student":
        return data["student_draft"].get(key)
    return None


def _format_field_value(key: str, value: Any, unit: str | None) -> str:
    if value is None:
        return "off"
    if key == "notification_lessons_today":
        return digest_clock(value)
    if key == "working_days":
        return ", ".join(DAY_NAMES[day - 1] for day in value)
    if key == "notification_lesson":
        if unit == "hours" and value % 60 == 0:
            return f"{value // 60} hours"
        return f"{value} minutes"
    if key == "notification_homework":
        return f"{value} days"
    if key in {"lesson_length", "between_lessons_break"}:
        return f"{value} minutes"
    return str(value)


async def field_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    title, hint = _field_prompt(dialog_manager)
    key = str(data["field_key"])
    value = _format_field_value(
        key, _current_field_value(data), data.get("reminder_unit")
    )
    return {
        "title": escape(title),
        "hint": escape(hint),
        "value": escape(value),
        "timezone": escape(
            str(data.get("view", {}).get("account", {}).get("timezone", ""))
        ),
        "feedback": _feedback(data),
        "notice": escape(str(data.get("notice", ""))),
        "can_save": not data.get("needs_reload") and not data.get("saving"),
    }


async def field_input(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    data = manager.dialog_data
    key = data["field_key"]
    try:
        if key == "timezone":
            parsed: Any = parse_timezone(value)
        elif key in {
            "notification_lessons_today",
            "work_start",
            "work_end",
        }:
            clock = parse_clock(value)
            if key == "notification_lessons_today":
                hour, minute = (int(part) for part in clock.split(":"))
                parsed = hour * 60 + minute
            else:
                parsed = clock
        elif key == "notification_lesson":
            unit = data.get("reminder_unit")
            if unit not in {"hours", "minutes"}:
                raise ValueError("Choose hours or minutes before entering a value.")
            number = parse_integer(value, maximum=10 if unit == "hours" else 600)
            parsed = number * 60 if unit == "hours" else number
        elif key == "between_lessons_break":
            parsed = parse_integer(value, minimum=0)
        else:
            parsed = parse_integer(value)
    except ValueError as exc:
        data["error"] = str(exc)
        manager.show_mode = ShowMode.EDIT
        return

    section = data["section"]
    if section == "account":
        data["account_draft"][key] = parsed
    elif section == "teacher":
        data["teacher_draft"][key] = parsed
    elif section == "student":
        data["student_draft"][key] = parsed
    else:
        raise ValueError("Settings section is missing")
    data.update(error="", notice="")
    await manager.switch_to(_section_state(section), show_mode=ShowMode.EDIT)


async def field_input_error(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    error: ValueError,
) -> None:
    manager.dialog_data["error"] = str(error)
    manager.show_mode = ShowMode.EDIT


async def edit_working_days(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(section="teacher", error="")
    widget = manager.find("working_days_select")
    if not isinstance(widget, ManagedMultiselect):
        raise TypeError("Working-days widget missing")
    await manager.switch_to(SettingsScreens.days, show_mode=ShowMode.EDIT)
    selected = set(manager.dialog_data["teacher_draft"]["working_days"])
    for day in range(1, 8):
        await widget.set_checked(day, day in selected)
    await manager.show(show_mode=ShowMode.EDIT)


async def save_working_days(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    widget = manager.find("working_days_select")
    if not isinstance(widget, ManagedMultiselect):
        raise TypeError("Working-days widget missing")
    selected = sorted(int(day) for day in widget.get_checked())
    if not selected:
        manager.dialog_data["error"] = "Select at least one working day."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data["teacher_draft"]["working_days"] = selected
    manager.dialog_data.update(error="", notice="")
    await manager.switch_to(SettingsScreens.teacher, show_mode=ShowMode.EDIT)


def _validate_teacher_draft(draft: dict[str, Any]) -> None:
    if not draft["working_days"]:
        raise ValueError("Select at least one working day.")
    work_start, work_end = draft["work_start"], draft["work_end"]
    if work_start is None or work_end is None:
        raise ValueError("Configure both work start and work end.")
    if parse_clock(work_end) <= parse_clock(work_start):
        raise ValueError("Work end must be later than work start.")
    if draft["max_consecutive_lessons"] > draft["max_lessons_per_day"]:
        raise ValueError(
            "Consecutive lesson limit cannot exceed the daily lesson limit."
        )


async def _complete_save(
    callback: CallbackQuery, manager: DialogManager, success_message: str
) -> None:
    data = manager.dialog_data
    if callback.message is None:
        raise ValueError("Settings require a private chat")
    try:
        view = await _backend(manager).view(
            callback.from_user.id, callback.message.chat.id
        )
        refreshed = _settings_data(view)
    except (BackendError, BackendUnavailable, ValueError) as exc:
        data.update(
            saving=False,
            needs_reload=True,
            error=str(exc),
            warning=f"{success_message} Current settings could not be reloaded.",
            notice="",
        )
        await manager.switch_to(SettingsScreens.menu, show_mode=ShowMode.EDIT)
        return
    data.update(refreshed)
    data.update(
        section=None,
        saving=False,
        needs_reload=False,
        notice=f"{success_message} Current settings were reloaded.",
        error="",
        warning="",
    )
    await manager.switch_to(SettingsScreens.menu, show_mode=ShowMode.EDIT)


async def _write_failed(
    manager: DialogManager, error: BackendError | BackendUnavailable
) -> None:
    data = manager.dialog_data
    data["saving"] = False
    if isinstance(error, BackendOutcomeUnknown):
        data.update(
            needs_reload=True,
            warning=(
                "The save outcome is unknown. Reload the latest settings before "
                "trying again; the write will not be repeated."
            ),
            error="",
            notice="",
        )
        await manager.switch_to(SettingsScreens.menu, show_mode=ShowMode.EDIT)
        return
    data.update(error=str(error), notice="")
    await manager.switch_to(_section_state(data["section"]), show_mode=ShowMode.EDIT)


async def save_account(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Settings require a private chat")
    data = manager.dialog_data
    if data.get("needs_reload") or data.get("saving"):
        return
    changes = _changed(data["account_original"], data["account_draft"])
    if not changes:
        data["error"] = "There are no account changes to save."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    try:
        payload = SelfProfileUpdate.model_validate(changes)
    except ValidationError as exc:
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data.update(saving=True, error="", notice="")
    try:
        await _backend(manager).update_me(
            callback.from_user.id, callback.message.chat.id, payload
        )
    except (BackendError, BackendUnavailable) as exc:
        await _write_failed(manager, exc)
        return
    await _complete_save(callback, manager, "Account settings saved.")


async def save_teacher(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Settings require a private chat")
    data = manager.dialog_data
    if data.get("needs_reload") or data.get("saving"):
        return
    changes = _changed(data["teacher_original"], data["teacher_draft"])
    if not changes:
        data["error"] = "There are no teacher-profile changes to save."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    try:
        _validate_teacher_draft(data["teacher_draft"])
        payload = TeacherProfileUpdate.model_validate(changes)
    except (ValueError, ValidationError) as exc:
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data.update(saving=True, error="", notice="")
    view = _settings_view(manager)
    if view.account.role != "TEACHER":
        raise ValueError("Only teachers can update teacher settings")
    try:
        await _backend(manager).update_teacher_profile(
            callback.from_user.id, callback.message.chat.id, view.account.id, payload
        )
    except (BackendError, BackendUnavailable) as exc:
        await _write_failed(manager, exc)
        return
    success_message = "Teacher settings saved."
    if "between_lessons_break" in changes:
        success_message += (
            " This change applies to new lessons only; existing lessons and "
            "series are unchanged."
        )
    await _complete_save(callback, manager, success_message)


async def save_student(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Settings require a private chat")
    data = manager.dialog_data
    if data.get("needs_reload") or data.get("saving"):
        return
    changes = _changed(data["student_original"], data["student_draft"])
    if not changes:
        data["error"] = "There are no student-notification changes to save."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    try:
        payload = StudentProfileUpdate.model_validate(changes)
    except ValidationError as exc:
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data.update(saving=True, error="", notice="")
    view = _settings_view(manager)
    if view.account.role != "STUDENT":
        raise ValueError("Only students can update student settings")
    try:
        await _backend(manager).update_student_profile(
            callback.from_user.id, callback.message.chat.id, view.account.id, payload
        )
    except (BackendError, BackendUnavailable) as exc:
        await _write_failed(manager, exc)
        return
    await _complete_save(callback, manager, "Student notification settings saved.")


async def _reload_settings(callback: CallbackQuery, manager: DialogManager) -> None:
    if callback.message is None:
        raise ValueError("Settings require a private chat")
    try:
        view = await _backend(manager).view(
            callback.from_user.id, callback.message.chat.id
        )
        refreshed = _settings_data(view)
    except (BackendError, BackendUnavailable, ValueError) as exc:
        manager.dialog_data.update(
            needs_reload=True,
            error=f"Could not reload settings: {exc}",
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(refreshed)
    manager.dialog_data.update(
        notice="Latest settings loaded from the backend.",
        error="",
        needs_reload=False,
    )
    await manager.switch_to(SettingsScreens.menu, show_mode=ShowMode.EDIT)


async def reload_settings(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _reload_settings(callback, manager)


async def retry_settings(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _reload_settings(callback, manager)


async def menu_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    view = SessionView.model_validate(data["view"]) if data.get("view") else None
    role = data.get("role")
    return {
        "role": escape(str(role or "")),
        "notice": escape(str(data.get("notice", ""))),
        "feedback": _feedback(data),
        "can_open": not data.get("needs_reload") and view is not None,
        "is_teacher": role == "TEACHER",
        "is_student": role == "STUDENT",
        "can_reload": bool(data.get("needs_reload")),
        "can_return_home": True,
    }


async def account_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    draft = data["account_draft"]
    return {
        "timezone": escape(str(draft["timezone"])),
        "digest": escape(digest_clock(draft["notification_lessons_today"])),
        "notice": escape(str(data.get("notice", ""))),
        "feedback": _feedback(data),
        "can_save": bool(
            _changed(data["account_original"], draft)
            and not data.get("needs_reload")
            and not data.get("saving")
        ),
        "can_edit": not data.get("needs_reload") and not data.get("saving"),
        "can_disable_digest": (
            draft["notification_lessons_today"] is not None
            and not data.get("needs_reload")
            and not data.get("saving")
        ),
    }


async def teacher_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    draft = data["teacher_draft"]
    lines = [
        f"{TEACHER_FIELD_LABELS[field]}: "
        f"{_format_field_value(field, draft[field], None)}"
        for field in TEACHER_FIELDS
    ]
    lines.insert(
        2,
        "Working days: "
        + ", ".join(DAY_NAMES[day - 1] for day in draft["working_days"]),
    )
    return {
        "profile": escape("\n".join(lines)),
        "notice": escape(str(data.get("notice", ""))),
        "feedback": _feedback(data),
        "can_save": bool(
            _changed(data["teacher_original"], draft)
            and not data.get("needs_reload")
            and not data.get("saving")
        ),
        "can_edit": not data.get("needs_reload") and not data.get("saving"),
    }


async def student_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    draft = data["student_draft"]
    return {
        "reminder": escape(
            _format_field_value(
                "notification_lesson", draft["notification_lesson"], None
            )
        ),
        "homework": escape(
            _format_field_value(
                "notification_homework", draft["notification_homework"], None
            )
        ),
        "notice": escape(str(data.get("notice", ""))),
        "feedback": _feedback(data),
        "can_save": bool(
            _changed(data["student_original"], draft)
            and not data.get("needs_reload")
            and not data.get("saving")
        ),
        "can_edit": not data.get("needs_reload") and not data.get("saving"),
        "can_disable_lesson": (
            draft["notification_lesson"] is not None
            and not data.get("needs_reload")
            and not data.get("saving")
        ),
        "can_disable_homework": (
            draft["notification_homework"] is not None
            and not data.get("needs_reload")
            and not data.get("saving")
        ),
    }


async def days_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    return {
        "days": [(name, index + 1) for index, name in enumerate(DAY_NAMES)],
        "feedback": _feedback(dialog_manager.dialog_data),
        "can_apply": not dialog_manager.dialog_data.get("needs_reload")
        and not dialog_manager.dialog_data.get("saving"),
    }


async def reminder_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    value = data["student_draft"]["notification_lesson"]
    return {
        "current": escape(_format_field_value("notification_lesson", value, None)),
        "feedback": _feedback(data),
        "can_edit": not data.get("needs_reload") and not data.get("saving"),
    }


async def unavailable_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    return {"feedback": _feedback(dialog_manager.dialog_data)}


def build_settings_dialog() -> Dialog:
    return Dialog(
        Window(
            Format(
                "{feedback}{role} settings\n"
                "Account and role preferences save separately. Notifications are "
                "preferences only; delivery is not active.\n"
                "{notice}"
            ),
            Button(
                Const("Account settings"),
                id="account",
                on_click=open_account,
                when="can_open",
            ),
            Button(
                Const("Teacher settings"),
                id="teacher",
                on_click=open_role_settings,
                when="is_teacher",
            ),
            Button(
                Const("Student notifications"),
                id="student",
                on_click=open_role_settings,
                when="is_student",
            ),
            Button(
                Const("Reload settings"),
                id="reload",
                on_click=reload_settings,
                when="can_reload",
            ),
            Button(
                Const("Refresh settings"),
                id="refresh",
                on_click=reload_settings,
                when="can_open",
            ),
            Button(
                Const("Back to home"),
                id="home",
                on_click=return_home,
                when="can_return_home",
            ),
            state=SettingsScreens.menu,
            getter=menu_data,
        ),
        Window(
            Format(
                "{feedback}Account preferences\nTimezone: {timezone}\n"
                "Daily digest (local): {digest}\n"
                "Timezone changes do not move stored event instants.\n{notice}"
            ),
            Button(
                Const("Change timezone"),
                id="timezone",
                on_click=edit_field,
                when="can_edit",
            ),
            Button(
                Const("Change daily digest"),
                id="notification_lessons_today",
                on_click=edit_field,
                when="can_edit",
            ),
            Button(
                Const("Disable daily digest"),
                id="disable_digest",
                on_click=disable_digest,
                when="can_disable_digest",
            ),
            Button(
                Const("Save account settings"),
                id="save",
                on_click=save_account,
                when="can_save",
            ),
            Button(Const("Cancel changes"), id="cancel", on_click=cancel_section),
            Button(Const("Back"), id="back", on_click=back_to_menu),
            state=SettingsScreens.account,
            getter=account_data,
        ),
        Window(
            Format(
                "{feedback}Teacher preferences\n{profile}\n"
                "Break changes apply to new lessons only; existing lessons and "
                "series keep their saved break duration.\n"
                "{notice}"
            ),
            Button(
                Const("Work starts"),
                id="work_start",
                on_click=edit_field,
                when="can_edit",
            ),
            Button(
                Const("Work ends"),
                id="work_end",
                on_click=edit_field,
                when="can_edit",
            ),
            Button(
                Const("Working days"),
                id="working_days",
                on_click=edit_working_days,
                when="can_edit",
            ),
            Button(
                Const("Lesson length"),
                id="lesson_length",
                on_click=edit_field,
                when="can_edit",
            ),
            Button(
                Const("Between-lesson break"),
                id="between_lessons_break",
                on_click=edit_field,
                when="can_edit",
            ),
            Button(
                Const("Consecutive lesson limit"),
                id="max_consecutive_lessons",
                on_click=edit_field,
                when="can_edit",
            ),
            Button(
                Const("Lessons per day"),
                id="max_lessons_per_day",
                on_click=edit_field,
                when="can_edit",
            ),
            Button(
                Const("Save teacher settings"),
                id="save",
                on_click=save_teacher,
                when="can_save",
            ),
            Button(Const("Cancel changes"), id="cancel", on_click=cancel_section),
            Button(Const("Back"), id="back", on_click=back_to_menu),
            state=SettingsScreens.teacher,
            getter=teacher_data,
        ),
        Window(
            Format(
                "{feedback}Student notifications\nLesson reminder: {reminder}\n"
                "Homework reminder: {homework}\n"
                "These preferences do not mean notifications are being delivered.\n"
                "{notice}"
            ),
            Button(
                Const("Change lesson reminder"),
                id="notification_lesson",
                on_click=edit_field,
                when="can_edit",
            ),
            Button(
                Const("Disable lesson reminder"),
                id="disable_lesson",
                on_click=disable_lesson_reminder,
                when="can_disable_lesson",
            ),
            Button(
                Const("Change homework reminder"),
                id="notification_homework",
                on_click=edit_field,
                when="can_edit",
            ),
            Button(
                Const("Disable homework reminder"),
                id="disable_homework",
                on_click=disable_homework_reminder,
                when="can_disable_homework",
            ),
            Button(
                Const("Save student notifications"),
                id="save",
                on_click=save_student,
                when="can_save",
            ),
            Button(Const("Cancel changes"), id="cancel", on_click=cancel_section),
            Button(Const("Back"), id="back", on_click=back_to_menu),
            state=SettingsScreens.student,
            getter=student_data,
        ),
        Window(
            Format(
                "{feedback}{title}\n{hint}\nCurrent value: {value}\n"
                "Timezone: {timezone}\n{notice}"
            ),
            TextInput("value", on_success=field_input, on_error=field_input_error),
            Button(Const("Back"), id="back", on_click=back_to_section),
            state=SettingsScreens.field,
            getter=field_data,
        ),
        Window(
            Format("{feedback}Working days\nSelect at least one weekday."),
            Multiselect(
                Format("✓ {item[0]}"),
                Format("{item[0]}"),
                id="working_days_select",
                item_id_getter=lambda item: item[1],
                items="days",
                type_factory=int,
            ),
            Button(
                Const("Apply working days"),
                id="apply",
                on_click=save_working_days,
                when="can_apply",
            ),
            Button(Const("Back"), id="back", on_click=back_to_section),
            state=SettingsScreens.days,
            getter=days_data,
        ),
        Window(
            Format(
                "{feedback}Lesson reminder: {current}\n"
                "Choose the input unit explicitly."
            ),
            Row(
                Button(
                    Const("Minutes"),
                    id="minutes",
                    on_click=choose_reminder_unit,
                    when="can_edit",
                ),
                Button(
                    Const("Hours"),
                    id="hours",
                    on_click=choose_reminder_unit,
                    when="can_edit",
                ),
            ),
            Button(Const("Disable"), id="disable", on_click=disable_lesson_reminder),
            Button(Const("Back"), id="back", on_click=back_to_section),
            state=SettingsScreens.reminder_unit,
            getter=reminder_data,
        ),
        Window(
            Format("{feedback}Retry loading settings from the backend."),
            Button(Const("Reload settings"), id="retry", on_click=retry_settings),
            Button(Const("Back to home"), id="home", on_click=return_home),
            state=SettingsScreens.unavailable,
            getter=unavailable_data,
        ),
        on_start=settings_started,
    )

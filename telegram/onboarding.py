import re
from dataclasses import dataclass
from html import escape
from typing import Any

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.deep_linking import create_start_link
from aiogram_dialog import Dialog, DialogManager, ShowMode, StartMode, Window
from aiogram_dialog.widgets.input import ManagedTextInput, TextInput
from aiogram_dialog.widgets.kbd import Button, Multiselect, Row, Select
from aiogram_dialog.widgets.kbd.select import ManagedMultiselect
from aiogram_dialog.widgets.text import Const, Format

from telegram.api import BackendClient, BackendError, BackendUnavailable, Registration
from telegram.inputs import digest_clock, parse_clock, parse_integer, parse_timezone


class Wizard(StatesGroup):
    code = State()
    teacher = State()
    input = State()
    days = State()
    reminder_unit = State()
    review = State()


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    hint: str


TEACHER_STEPS = (
    Step(
        "timezone",
        "Timezone",
        "Enter an IANA timezone (for example Asia/Bangkok) or confirm UTC.",
    ),
    Step("work_start", "Work starts", "Enter HH:MM, HH MM or HH/MM."),
    Step("work_end", "Work ends", "Enter a same-day time later than work starts."),
    Step("working_days", "Working days", "Select at least one weekday."),
    Step("lesson_length", "Lesson length", "Enter a positive number of minutes."),
    Step(
        "between_lessons_break",
        "Between-lesson break",
        "Enter minutes; 0 disables the break.",
    ),
    Step(
        "max_consecutive_lessons",
        "Consecutive lessons",
        "Enter a positive lesson count.",
    ),
    Step(
        "max_lessons_per_day",
        "Lessons per day",
        "Must be at least the consecutive-lesson limit.",
    ),
    Step(
        "notification_lessons_today",
        "Daily digest",
        "Enter local HH:MM, HH MM or HH/MM, or disable.",
    ),
)
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
STUDENT_STEPS = (
    TEACHER_STEPS[0],
    TEACHER_STEPS[-1],
    Step(
        "notification_lesson",
        "Lesson reminder",
        "Choose hours or minutes explicitly. Maximum 10 hours / 600 minutes.",
    ),
    Step(
        "notification_homework",
        "Homework reminder",
        "Enter a positive number of days before the lesson, or disable.",
    ),
)


def steps(manager: DialogManager) -> tuple[Step, ...]:
    return (
        TEACHER_STEPS
        if manager.dialog_data["draft"]["role"] == "TEACHER"
        else STUDENT_STEPS
    )


def backend(manager: DialogManager) -> BackendClient:
    client = manager.middleware_data["backend_client"]
    if not isinstance(client, BackendClient):
        raise TypeError("Backend client is not configured")
    return client


def current_step(manager: DialogManager) -> Step:
    return steps(manager)[int(manager.dialog_data["index"])]


def setting_value(key: str, value: Any) -> str:
    if key == "notification_lessons_today":
        return digest_clock(value)
    if key == "working_days":
        return ", ".join(DAY_NAMES[day - 1] for day in value)
    if key in {"notification_lesson", "notification_homework"}:
        return (
            "off"
            if value is None
            else f"{value} {'minutes' if key == 'notification_lesson' else 'days'}"
        )
    return str(value)


async def wizard_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    draft = dialog_manager.dialog_data["draft"]
    step = current_step(dialog_manager)
    value = draft.get(step.key, "UTC (requires confirmation)")
    summary = "\n".join(
        f"{item.title}: {setting_value(item.key, draft.get(item.key, 'not confirmed'))}"
        for item in steps(dialog_manager)
    )
    if draft["role"] == "STUDENT":
        summary = f"Teacher: {draft.get('teacher_name', 'not confirmed')}\n{summary}"
    return {
        "title": step.title,
        "hint": step.hint
        + (
            f"\nInput unit: {draft.get('_lesson_unit', 'choose a unit first')}"
            if step.key == "notification_lesson"
            else ""
        ),
        "value": escape(setting_value(step.key, value)),
        "error": escape(dialog_manager.dialog_data.get("error", "")),
        "summary": escape(summary),
        "can_disable": step.key.startswith("notification_"),
        "days": [(name, index + 1) for index, name in enumerate(DAY_NAMES)],
        "edits": [
            (item.title, str(index)) for index, item in enumerate(steps(dialog_manager))
        ],
        "teacher_name": escape(draft.get("teacher_name", "not confirmed")),
    }


async def show_step(manager: DialogManager) -> None:
    manager.dialog_data["error"] = ""
    state = Wizard.days if current_step(manager).key == "working_days" else Wizard.input
    if current_step(manager).key == "notification_lesson":
        state = Wizard.reminder_unit
    if state == Wizard.days:
        widget = manager.find("working_days")
        if not isinstance(widget, ManagedMultiselect):
            raise TypeError("Working-days widget missing")
        for day in range(1, 8):
            await widget.set_checked(
                day, day in manager.dialog_data["draft"]["working_days"]
            )
    await manager.switch_to(state, show_mode=ShowMode.EDIT)


async def advance(manager: DialogManager) -> None:
    if (
        manager.dialog_data.get("editing")
        or manager.dialog_data["index"] == len(steps(manager)) - 1
    ):
        manager.dialog_data["editing"] = False
        await manager.switch_to(Wizard.review, show_mode=ShowMode.EDIT)
    else:
        manager.dialog_data["index"] += 1
        await show_step(manager)


async def begin_teacher(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.start(
        Wizard.input,
        data={"role": "TEACHER"},
        mode=StartMode.NORMAL,
        show_mode=ShowMode.EDIT,
    )


async def init_wizard(_data: Any, manager: DialogManager) -> None:
    manager.dialog_data.update(
        index=0,
        draft={
            "role": "TEACHER",
            "work_start": "09:00",
            "work_end": "18:00",
            "working_days": [1, 2, 3, 4, 5],
            "lesson_length": 60,
            "between_lessons_break": 5,
            "max_consecutive_lessons": 4,
            "max_lessons_per_day": 6,
            "notification_lessons_today": 540,
        },
    )
    if isinstance(_data, dict) and _data.get("role") == "STUDENT":
        manager.dialog_data["draft"] = {
            "role": "STUDENT",
            "notification_lessons_today": 540,
            "notification_lesson": 15,
            "notification_homework": 2,
        }
        code = _data.get("teacher_code")
        if code is not None:
            await resolve_code(manager, code)
        elif _data.get("invalid_invitation"):
            manager.dialog_data["error"] = "Invalid invitation. Enter a teacher code."


async def begin_student(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.start(Wizard.code, data={"role": "STUDENT"}, show_mode=ShowMode.EDIT)


async def resolve_code(manager: DialogManager, value: str) -> None:
    code = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,56}", code) is None:
        manager.dialog_data["error"] = (
            "Invalid teacher code. Use the code from your teacher."
        )
        await manager.switch_to(Wizard.code, show_mode=ShowMode.EDIT)
        return
    try:
        teacher = await backend(manager).resolve(code)
    except (BackendError, BackendUnavailable) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.switch_to(Wizard.code, show_mode=ShowMode.EDIT)
        return
    manager.dialog_data["draft"].update(
        teacher_code=code,
        teacher_name=teacher.full_name or "Teacher",
    )
    manager.dialog_data["error"] = ""
    await manager.switch_to(Wizard.teacher, show_mode=ShowMode.EDIT)


async def code_input(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    await resolve_code(manager, value)


async def confirm_teacher(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["index"] = 0
    await show_step(manager)


async def change_teacher(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.switch_to(Wizard.code, show_mode=ShowMode.EDIT)


async def select_unit(
    _callback: CallbackQuery, button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["draft"]["_lesson_unit"] = button.widget_id
    await manager.switch_to(Wizard.input, show_mode=ShowMode.EDIT)


async def default_reminder(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await advance(manager)


def parse_setting(step: Step, value: str, draft: dict[str, Any]) -> str | int:
    if step.key == "timezone":
        return parse_timezone(value)
    if step.key == "notification_lesson":
        unit = draft.get("_lesson_unit")
        if unit not in {"hours", "minutes"}:
            raise ValueError("Choose hours or minutes before entering a value.")
        result = parse_integer(value, maximum=10 if unit == "hours" else 600)
        return result * 60 if unit == "hours" else result
    if step.key == "notification_homework":
        return parse_integer(value)
    if step.key in {"work_start", "work_end", "notification_lessons_today"}:
        clock = parse_clock(value)
        if step.key == "work_end" and clock <= draft["work_start"]:
            raise ValueError("Work end must be later than work start.")
        if (
            step.key == "work_start"
            and draft.get("_review_edit")
            and clock >= draft["work_end"]
        ):
            raise ValueError("Work start must be earlier than work end.")
        if step.key == "notification_lessons_today":
            hour, minute = map(int, clock.split(":"))
            return hour * 60 + minute
        return clock
    result = parse_integer(
        value, minimum=0 if step.key == "between_lessons_break" else 1
    )
    if step.key == "max_lessons_per_day" and result < draft["max_consecutive_lessons"]:
        raise ValueError("Daily limit must be at least the consecutive-lesson limit.")
    if (
        step.key == "max_consecutive_lessons"
        and draft.get("_review_edit")
        and result > draft["max_lessons_per_day"]
    ):
        raise ValueError(
            "Consecutive limit cannot exceed the daily limit. Edit daily limit first."
        )
    return result


async def value_input(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    await apply_value(manager, value)


async def apply_value(manager: DialogManager, value: str) -> None:
    manager.show_mode = ShowMode.EDIT
    draft = manager.dialog_data["draft"]
    step = current_step(manager)
    try:
        parsed = parse_setting(step, value, draft)
    except ValueError as exc:
        manager.dialog_data["error"] = str(exc)
        return
    draft[step.key] = parsed
    manager.dialog_data["error"] = ""
    await advance(manager)


async def use_default(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    step = current_step(manager)
    draft = manager.dialog_data["draft"]
    value = draft.get(step.key, "UTC")
    if value is None:
        await advance(manager)
        return
    if step.key == "notification_lessons_today":
        value = digest_clock(value)
    if step.key == "notification_lesson":
        await advance(manager)
        return
    # Defaults are explicitly confirmed, not inferred from user language.
    await apply_value(manager, str(value))


async def disable(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["draft"][current_step(manager).key] = None
    await advance(manager)


async def choose_days(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    widget = manager.find("working_days")
    if not isinstance(widget, ManagedMultiselect):
        raise TypeError("Working-days widget missing")
    selected = sorted(int(value) for value in widget.get_checked())
    if not selected:
        manager.dialog_data["error"] = "Select at least one working day."
        return
    manager.dialog_data["draft"]["working_days"] = selected
    await advance(manager)


async def go_back(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["editing"] = False
    if (
        manager.dialog_data["index"] == 0
        and manager.dialog_data["draft"]["role"] == "STUDENT"
    ):
        await manager.switch_to(Wizard.teacher, show_mode=ShowMode.EDIT)
        return
    manager.dialog_data["index"] = max(0, manager.dialog_data["index"] - 1)
    await show_step(manager)


async def cancel(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.done(show_mode=ShowMode.EDIT)


async def edit_setting(
    _callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    manager.dialog_data.update(index=int(item), editing=True)
    manager.dialog_data["draft"]["_review_edit"] = True
    await show_step(manager)


async def submit(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("submitted"):
        return
    if callback.message is None:
        raise ValueError("Registration requires a private chat")
    draft = manager.dialog_data["draft"]
    data = Registration(
        telegram_user_id=callback.from_user.id,
        telegram_private_chat_id=callback.message.chat.id,
        full_name=callback.from_user.full_name,
        role=draft["role"],
        timezone=draft["timezone"],
        notification_lessons_today=draft["notification_lessons_today"],
        teacher_code=draft.get("teacher_code"),
        profile={
            item.key: draft[item.key]
            for item in steps(manager)
            if item.key not in {"timezone", "notification_lessons_today"}
        },
    )
    try:
        view = await backend(manager).register(data)
    except (BackendError, BackendUnavailable) as exc:
        manager.dialog_data["error"] = str(exc)
        return
    manager.dialog_data["submitted"] = True
    invitation = ""
    if view.teacher is not None:
        invitation = await create_start_link(
            manager.middleware_data["bot"], f"teacher_{view.teacher.code}"
        )
    await manager.done(
        result={"view": view.model_dump(mode="json"), "invitation": invitation},
        show_mode=ShowMode.EDIT,
    )


def build_wizard() -> Dialog:
    def navigation() -> Row:
        return Row(
            Button(Const("Back"), id="back", on_click=go_back),
            Button(Const("Cancel"), id="cancel", on_click=cancel),
        )

    return Dialog(
        Window(
            Format("Enter your teacher's invitation code.\n{error}"),
            TextInput("teacher_code", on_success=code_input),
            Button(Const("Cancel"), id="cancel", on_click=cancel),
            state=Wizard.code,
        ),
        Window(
            Format("Register as a student of {teacher_name}?\n{error}"),
            Button(
                Const("Confirm teacher"), id="confirm_teacher", on_click=confirm_teacher
            ),
            Button(Const("Change code"), id="change_code", on_click=change_teacher),
            Button(Const("Cancel"), id="cancel", on_click=cancel),
            state=Wizard.teacher,
        ),
        Window(
            Format("{title}\n{hint}\nCurrent/default: {value}\n{error}"),
            TextInput("value", on_success=value_input),
            Button(Const("Use current/default"), id="default", on_click=use_default),
            Button(
                Const("Disable"), id="disable", on_click=disable, when="can_disable"
            ),
            navigation(),
            state=Wizard.input,
        ),
        Window(
            Format("Working days\n{error}"),
            Multiselect(
                Format("✓ {item[0]}"),
                Format("{item[0]}"),
                id="working_days",
                item_id_getter=lambda item: item[1],
                items="days",
                type_factory=int,
            ),
            Button(Const("Next"), id="next", on_click=choose_days),
            navigation(),
            state=Wizard.days,
        ),
        Window(
            Format(
                "Lesson reminder\nCurrent/default: {value}\nChoose an input unit or confirm the displayed minute value.\n{error}"
            ),
            Row(
                Button(Const("Minutes"), id="minutes", on_click=select_unit),
                Button(Const("Hours"), id="hours", on_click=select_unit),
            ),
            Button(
                Const("Use current minutes/off"),
                id="default_reminder",
                on_click=default_reminder,
            ),
            Button(Const("Disable"), id="disable", on_click=disable),
            navigation(),
            state=Wizard.reminder_unit,
        ),
        Window(
            Format(
                "Review\n{summary}\n{error}\nPreferences are saved only. Notifications and schedule enforcement are not active."
            ),
            Select(
                Format("Edit {item[0]}"),
                id="edit",
                item_id_getter=lambda item: item[1],
                items="edits",
                on_click=edit_setting,
            ),
            Button(Const("Register"), id="submit", on_click=submit),
            navigation(),
            state=Wizard.review,
        ),
        getter=wizard_data,
        on_start=init_wizard,
    )

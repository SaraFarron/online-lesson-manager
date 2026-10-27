from datetime import date, datetime, time, timedelta
from html import escape
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, ShowMode, StartMode, Window
from aiogram_dialog.widgets.input import ManagedTextInput, TextInput
from aiogram_dialog.widgets.kbd import Button, Column, Row, Select
from aiogram_dialog.widgets.text import Const, Format
from pydantic import ValidationError

from telegram.api import (
    BackendError,
    BackendOutcomeUnknown,
    BackendUnavailable,
    EventCreatePayload,
    EventWriteResult,
    LocalIntervalResolution,
    RecurrentEventCreatePayload,
    RecurrentEventWriteResult,
    SessionView,
    StudentPickerPage,
)
from telegram.date_cleanup import delete_accepted_date_message
from telegram.inputs import (
    ParsedScheduleDate,
    combine_schedule_date_time,
    format_human_date,
    format_local_interval_in_timezone,
    format_local_interval_review,
    format_schedule_date_confirmation,
    parse_integer,
    parse_schedule_date,
    parse_schedule_time,
)
from telegram.onboarding import backend
from telegram.schedule_dialogs import open_schedule_week

STUDENT_PAGE_SIZE = 20


class LessonScreens(StatesGroup):
    subject = State()
    kind = State()
    duration = State()
    duration_input = State()
    start_date = State()
    confirm_start_date = State()
    start_time = State()
    cutoff_date = State()
    confirm_cutoff_date = State()
    review = State()
    unavailable = State()


async def open_lesson_creation(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Lesson creation requires a private chat")
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role not in {"TEACHER", "STUDENT"}:
        raise ValueError("This account cannot create lessons")

    data: dict[str, Any] = {
        "view": view.model_dump(mode="json"),
        "is_teacher": view.account.role == "TEACHER",
        "account_id": str(view.account.id),
        "viewer_timezone": view.account.timezone,
        "subject_id": None,
        "subject_label": None,
        "kind": None,
        "duration_minutes": None,
        "custom_duration": False,
        "start_date": None,
        "resolution": None,
        "cutoff_date": None,
        "interval_end": None,
        "error": "",
        "submitted": False,
        "submitting": False,
        "outcome_unknown": False,
        "needs_schedule_reload": False,
    }

    try:
        client = backend(manager)
        if view.account.role == "TEACHER":
            if view.teacher is None:
                raise BackendUnavailable()
            data.update(
                teacher_id=str(view.account.id),
                teacher_timezone=view.account.timezone,
                default_duration=view.teacher.lesson_length,
                duration_minutes=view.teacher.lesson_length,
                between_lessons_break=view.teacher.between_lessons_break,
                subject_id=str(view.account.id),
                subject_label="Personal lesson",
            )
            state = LessonScreens.kind
        else:
            context = await client.read_teacher_booking_context(
                callback.from_user.id, callback.message.chat.id
            )
            if context.teacher_id != view.account.teacher_id:
                raise BackendUnavailable()
            data.update(
                subject_id=str(view.account.id),
                subject_label="My lesson",
                teacher_id=str(context.teacher_id),
                teacher_timezone=context.timezone,
                default_duration=context.lesson_length,
                duration_minutes=context.lesson_length,
                between_lessons_break=context.between_lessons_break,
            )
            state = LessonScreens.kind
    except (BackendError, BackendUnavailable) as exc:
        data["error"] = str(exc)
        state = LessonScreens.unavailable

    await manager.start(
        state,
        data=data,
        mode=StartMode.NORMAL,
        show_mode=ShowMode.EDIT,
    )


def _store_picker_page(
    data: dict[str, Any], page: StudentPickerPage, skip: int
) -> None:
    data.update(
        picker_items=[item.model_dump(mode="json") for item in page.items],
        picker_total=page.total,
        picker_skip=skip,
    )


async def lesson_started(start_data: Any, manager: DialogManager) -> None:
    manager.dialog_data.clear()
    manager.dialog_data.update(start_data)


async def lesson_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    reference_date = datetime.now(ZoneInfo(data["viewer_timezone"])).date()
    resolution_data = data.get("resolution")
    review = ""
    if resolution_data is not None:
        try:
            resolution = _resolution(resolution_data)
            lesson_end = resolution.start.utc + timedelta(
                minutes=int(data["duration_minutes"])
            )
            interval = format_local_interval_in_timezone(
                resolution.start.utc,
                lesson_end,
                data["viewer_timezone"],
                reference_date=reference_date,
            )
            review = f"New lesson at {interval}\nTimezone: {data['viewer_timezone']}"
        except KeyError, TypeError, ValueError:
            review = "The lesson interval is invalid. Edit the duration or start time."
        except OverflowError:
            review = "The lesson interval is invalid. Edit the duration or start time."

    if data.get("kind") == "weekly" and data.get("cutoff_date"):
        teacher_timezone_note = (
            f"; weekly starts use {data['teacher_timezone']}"
            if data["teacher_timezone"] != data["viewer_timezone"]
            else ""
        )
        review += (
            f"\nWeekly through "
            f"{format_human_date(date.fromisoformat(data['cutoff_date']), reference_date=reference_date)} "
            f"inclusive{teacher_timezone_note}."
        )
    if data.get("custom_duration"):
        review += (
            "\nCustom duration applies only to this lesson or series; it does not "
            "change the teacher's default duration or between-lesson break."
        )
    if resolution_data is not None:
        try:
            break_minutes = int(data["between_lessons_break"])
            if break_minutes == 0:
                frequency = (
                    "each lesson" if data.get("kind") == "weekly" else "this lesson"
                )
                review += f"\nNo automatic break is reserved after {frequency}."
            else:
                if data.get("kind") == "weekly":
                    review += f"\nAutobreak after each lesson: {break_minutes} minutes."
                else:
                    review += f"\nAutobreak after new lesson: {break_minutes} minutes."
        except KeyError, TypeError, ValueError, OverflowError:
            review += "\nThe automatic break interval could not be displayed."

    items = data.get("picker_items", [])
    total = int(data.get("picker_total", 0))
    skip = int(data.get("picker_skip", 0))
    page_count = max(1, (total + STUDENT_PAGE_SIZE - 1) // STUDENT_PAGE_SIZE)
    page_number = skip // STUDENT_PAGE_SIZE + 1
    students = [(escape(item["display_label"]), str(item["id"])) for item in items]

    start_date = data.get("start_date")
    cutoff_date = data.get("cutoff_date")
    duration_minutes = int(
        data.get("duration_minutes") or data.get("default_duration", 0)
    )
    duration_summary = (
        f"Selected duration: {duration_minutes} minutes."
        if data.get("custom_duration")
        else f"Uses the default duration: {duration_minutes} minutes."
    )
    if data.get("is_teacher") and data.get("subject_id") == data.get("account_id"):
        kind_assignee_text = "A personal lesson is assigned to you by default."
    else:
        kind_assignee_text = f"Assigned to {data.get('subject_label') or 'you'}."
    return {
        "students": students,
        "picker_page": f"Page {page_number} of {page_count} ({total} students)",
        "personal_label": "personal lesson",
        "default_duration": data.get("default_duration", ""),
        "duration_summary": duration_summary,
        "kind_assignee_text": kind_assignee_text,
        "has_previous_students": skip > 0,
        "has_next_students": skip + len(items) < total,
        "error": escape(str(data.get("error", ""))),
        "subject_label": escape(str(data.get("subject_label") or "")),
        "review": escape(review),
        "start_date_hint": escape(
            f"Enter a local date in {data.get('viewer_timezone', 'your timezone')}."
        ),
        "start_date_confirmation": escape(
            format_schedule_date_confirmation(
                ParsedScheduleDate(
                    date.fromisoformat(start_date),
                    inferred_year=True,
                    reference_date=reference_date,
                )
            )
            if start_date
            else ""
        ),
        "cutoff_date_confirmation": escape(
            format_schedule_date_confirmation(
                ParsedScheduleDate(
                    date.fromisoformat(cutoff_date),
                    inferred_year=True,
                    reference_date=reference_date,
                )
            )
            if cutoff_date
            else ""
        ),
        "can_submit": (
            not data.get("submitted")
            and not data.get("submitting")
            and not data.get("needs_schedule_reload")
        ),
        "can_edit": (
            not data.get("submitted")
            and not data.get("submitting")
            and not data.get("needs_schedule_reload")
        ),
        "can_edit_duration": (
            not data.get("submitted")
            and not data.get("submitting")
            and not data.get("needs_schedule_reload")
            and bool(data.get("is_teacher"))
        ),
        "can_edit_subject": (
            not data.get("submitted")
            and not data.get("submitting")
            and not data.get("needs_schedule_reload")
            and bool(data.get("is_teacher"))
        ),
        "can_edit_cutoff": (
            not data.get("submitted")
            and not data.get("submitting")
            and not data.get("needs_schedule_reload")
            and data.get("kind") == "weekly"
        ),
        "can_reload": bool(
            data.get("outcome_unknown") or data.get("needs_schedule_reload")
        ),
    }


async def select_student(
    _callback: CallbackQuery, _widget: Select[str], manager: DialogManager, item: str
) -> None:
    if not manager.dialog_data.get("is_teacher"):
        raise ValueError("Only teachers can change the lesson assignee.")
    selected = next(
        (
            student
            for student in manager.dialog_data.get("picker_items", [])
            if str(student["id"]) == item
        ),
        None,
    )
    if selected is None:
        manager.dialog_data["error"] = "Select a student from the current page."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(
        subject_id=item,
        subject_label=selected["display_label"],
        error="",
    )
    await _return_from_subject_picker(manager)


async def choose_personal_lesson(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if not manager.dialog_data.get("is_teacher"):
        raise ValueError("Only teachers can change the lesson assignee.")
    manager.dialog_data.update(
        subject_id=manager.dialog_data["account_id"],
        subject_label="Personal lesson",
        error="",
    )
    await _return_from_subject_picker(manager)


async def cancel_subject_picker(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["error"] = ""
    await _return_from_subject_picker(manager)


async def _return_from_subject_picker(manager: DialogManager) -> None:
    return_to = manager.dialog_data.pop("subject_return_state", "kind")
    state = LessonScreens.review if return_to == "review" else LessonScreens.kind
    await manager.switch_to(state, show_mode=ShowMode.EDIT)


async def previous_students(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _change_student_page(callback, manager, -STUDENT_PAGE_SIZE)


async def next_students(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _change_student_page(callback, manager, STUDENT_PAGE_SIZE)


async def _change_student_page(
    callback: CallbackQuery, manager: DialogManager, offset: int
) -> None:
    if callback.message is None:
        raise ValueError("Student selection requires a private chat")
    data = manager.dialog_data
    skip = max(0, int(data.get("picker_skip", 0)) + offset)
    try:
        page = await backend(manager).read_student_picker(
            callback.from_user.id,
            callback.message.chat.id,
            skip=skip,
            limit=STUDENT_PAGE_SIZE,
        )
    except BackendError as exc:
        data["error"] = str(exc)
    except BackendUnavailable as exc:
        data["error"] = str(exc)
    else:
        _store_picker_page(data, page, skip)
        data["error"] = ""
    await manager.show(show_mode=ShowMode.EDIT)


async def choose_once(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _choose_kind(manager, "once")


async def choose_weekly(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _choose_kind(manager, "weekly")


async def _choose_kind(manager: DialogManager, kind: str) -> None:
    manager.dialog_data.update(kind=kind, error="")
    await manager.switch_to(LessonScreens.start_date, show_mode=ShowMode.EDIT)


async def use_default_duration(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if not manager.dialog_data.get("is_teacher"):
        raise ValueError("Only teachers can change the lesson duration.")
    manager.dialog_data.update(
        duration_minutes=manager.dialog_data["default_duration"],
        custom_duration=False,
        error="",
    )
    await _return_from_duration_edit(manager)


async def custom_duration(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if not manager.dialog_data.get("is_teacher"):
        raise ValueError("Only teachers can change the lesson duration.")
    manager.dialog_data["error"] = ""
    await manager.switch_to(LessonScreens.duration_input, show_mode=ShowMode.EDIT)


async def duration_input(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    if not manager.dialog_data.get("is_teacher"):
        raise ValueError("Only teachers can change the lesson duration.")
    try:
        minutes = parse_integer(value)
    except (OverflowError, ValueError) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(duration_minutes=minutes, custom_duration=True, error="")
    await _return_from_duration_edit(manager)


async def cancel_duration_edit(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["error"] = ""
    await _return_from_duration_edit(manager)


async def _return_from_duration_edit(manager: DialogManager) -> None:
    return_to = manager.dialog_data.pop("duration_return_state", "start_date")
    state = LessonScreens.review if return_to == "review" else LessonScreens.start_date
    await manager.switch_to(state, show_mode=ShowMode.EDIT)


async def start_date_input(
    message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    try:
        parsed = _parse_local_date(manager, value)
    except (OverflowError, ValueError) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(
        start_date=parsed.value.isoformat(),
        error="",
    )
    await delete_accepted_date_message(message, manager)
    state = (
        LessonScreens.confirm_start_date
        if parsed.requires_confirmation
        else LessonScreens.start_time
    )
    await manager.switch_to(state, show_mode=ShowMode.EDIT)


async def confirm_start_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.switch_to(LessonScreens.start_time, show_mode=ShowMode.EDIT)


async def start_time_input(
    message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    if message.from_user is None:
        raise ValueError("Lesson creation requires a private chat")
    try:
        clock = parse_schedule_time(value)
        local_start = combine_schedule_date_time(
            date.fromisoformat(manager.dialog_data["start_date"]), clock
        )
        probe_end = local_start + timedelta(minutes=1)
        resolution = await backend(manager).resolve_local_interval(
            message.from_user.id,
            message.chat.id,
            local_start,
            probe_end,
        )
        format_local_interval_review(
            resolution,
            reference_date=datetime.now(
                ZoneInfo(manager.dialog_data["viewer_timezone"])
            ).date(),
            duration_minutes=int(manager.dialog_data["duration_minutes"]),
        )
    except (OverflowError, ValueError) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except (BackendError, BackendUnavailable) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return

    manager.dialog_data.update(
        resolution=resolution.model_dump(mode="json"),
        error="",
    )
    state = (
        LessonScreens.cutoff_date
        if manager.dialog_data["kind"] == "weekly"
        else LessonScreens.review
    )
    await manager.switch_to(state, show_mode=ShowMode.EDIT)


async def cutoff_date_input(
    message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    if message.from_user is None:
        raise ValueError("Series end date requires a private chat")
    try:
        parsed = _parse_local_date(manager, value)
        if parsed.value < date.fromisoformat(manager.dialog_data["start_date"]):
            raise ValueError("The series end date cannot be before its first lesson.")
    except ValueError as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(
        cutoff_date=parsed.value.isoformat(),
        error="",
    )
    await delete_accepted_date_message(message, manager)
    if parsed.requires_confirmation:
        await manager.switch_to(
            LessonScreens.confirm_cutoff_date, show_mode=ShowMode.EDIT
        )
        return
    await _resolve_cutoff_date(message.from_user.id, message.chat.id, manager)


async def confirm_cutoff_date(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Series end date requires a private chat")
    await _resolve_cutoff_date(callback.from_user.id, callback.message.chat.id, manager)


async def _resolve_cutoff_date(
    sender_id: int, chat_id: int, manager: DialogManager
) -> None:
    try:
        next_day = date.fromisoformat(manager.dialog_data["cutoff_date"]) + timedelta(
            days=1
        )
        boundary = datetime.combine(next_day, time.min)
        probe_start = boundary - timedelta(minutes=1)
        resolution = await backend(manager).resolve_local_interval(
            sender_id,
            chat_id,
            probe_start,
            boundary,
        )
        cutoff = resolution.end.utc
        start_utc = _resolution(manager.dialog_data["resolution"]).start.utc
        if cutoff <= start_utc:
            raise ValueError("The series end date must include the first lesson.")
    except (OverflowError, ValueError) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except (BackendError, BackendUnavailable) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return

    manager.dialog_data.update(interval_end=cutoff.isoformat(), error="")
    await manager.switch_to(LessonScreens.review, show_mode=ShowMode.EDIT)


async def edit_start_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(
        start_date=None,
        resolution=None,
        cutoff_date=None,
        interval_end=None,
        error="",
    )
    await manager.switch_to(LessonScreens.start_date, show_mode=ShowMode.EDIT)


async def edit_subject(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _open_subject_picker(callback, manager, "review")


async def edit_subject_from_kind(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _open_subject_picker(callback, manager, "kind")


async def _open_subject_picker(
    callback: CallbackQuery, manager: DialogManager, return_to: str
) -> None:
    if not manager.dialog_data.get("is_teacher"):
        raise ValueError("Only teachers can change the lesson assignee.")
    if callback.message is None:
        raise ValueError("Student selection requires a private chat")
    try:
        page = await backend(manager).read_student_picker(
            callback.from_user.id,
            callback.message.chat.id,
            limit=STUDENT_PAGE_SIZE,
        )
    except BackendError as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    _store_picker_page(manager.dialog_data, page, 0)
    manager.dialog_data.update(subject_return_state=return_to, error="")
    await manager.switch_to(LessonScreens.subject, show_mode=ShowMode.EDIT)


async def edit_start_time(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(resolution=None, error="")
    await manager.switch_to(LessonScreens.start_time, show_mode=ShowMode.EDIT)


async def edit_duration(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _begin_duration_edit(manager, "review")


async def edit_duration_from_start_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _begin_duration_edit(manager, "start_date")


async def _begin_duration_edit(manager: DialogManager, return_to: str) -> None:
    if not manager.dialog_data.get("is_teacher"):
        raise ValueError("Only teachers can change the lesson duration.")
    manager.dialog_data.update(duration_return_state=return_to, error="")
    await manager.switch_to(LessonScreens.duration, show_mode=ShowMode.EDIT)


async def edit_cutoff_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(cutoff_date=None, interval_end=None, error="")
    await manager.switch_to(LessonScreens.cutoff_date, show_mode=ShowMode.EDIT)


async def submit_lesson(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    if data.get("submitted") or data.get("submitting"):
        return
    if callback.message is None:
        raise ValueError("Lesson creation requires a private chat")

    data.update(submitting=True, error="")
    try:
        resolution = _resolution(data["resolution"])
        start = resolution.start.utc
        subject_id = UUID(data["subject_id"])
        duration = int(data["duration_minutes"])
        end = _lesson_end(start, duration) if data["custom_duration"] else None
        client = backend(manager)
        if data["kind"] == "weekly":
            if end is None:
                payload: EventCreatePayload | RecurrentEventCreatePayload = (
                    RecurrentEventCreatePayload(
                        start=start,
                        user_id=subject_id,
                        event_type="LESSON",
                        interval=7,
                        interval_end=datetime.fromisoformat(data["interval_end"]),
                    )
                )
            else:
                payload = RecurrentEventCreatePayload(
                    start=start,
                    end=end,
                    user_id=subject_id,
                    event_type="LESSON",
                    interval=7,
                    interval_end=datetime.fromisoformat(data["interval_end"]),
                )
            await client.write_management_event(
                "POST",
                "recurrent-lessons",
                callback.from_user.id,
                callback.message.chat.id,
                payload,
                RecurrentEventWriteResult,
            )
        else:
            if end is None:
                payload = EventCreatePayload(
                    start=start,
                    user_id=subject_id,
                    event_type="LESSON",
                )
            else:
                payload = EventCreatePayload(
                    start=start,
                    end=end,
                    user_id=subject_id,
                    event_type="LESSON",
                )
            await client.write_management_event(
                "POST",
                "lessons",
                callback.from_user.id,
                callback.message.chat.id,
                payload,
                EventWriteResult,
            )
    except BackendOutcomeUnknown as exc:
        data.update(
            submitting=False, submitted=True, outcome_unknown=True, error=str(exc)
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendError as exc:
        data.update(
            submitting=False,
            needs_schedule_reload=exc.status == 409,
            error=str(exc),
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        data.update(submitting=False, error=str(exc))
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except (ValidationError, ValueError, OverflowError) as exc:
        data.update(
            submitting=False,
            error=f"Review could not be submitted: {exc}",
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return

    data.update(submitting=False, submitted=True)
    start_day = date.fromisoformat(data["start_date"])
    await manager.done(show_mode=ShowMode.EDIT)
    await open_schedule_week(callback, manager, start_day)


async def reload_schedule_after_write(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    start_day = date.fromisoformat(manager.dialog_data["start_date"])
    await manager.done(show_mode=ShowMode.EDIT)
    await open_schedule_week(callback, manager, start_day)


async def return_home(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.done(show_mode=ShowMode.EDIT)


async def _date_input_error(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    error: ValueError,
) -> None:
    manager.dialog_data["error"] = str(error)
    manager.show_mode = ShowMode.EDIT


def _parse_local_date(manager: DialogManager, value: str) -> ParsedScheduleDate:
    timezone = ZoneInfo(manager.dialog_data["viewer_timezone"])
    today = datetime.now(timezone).date()
    return parse_schedule_date(value, today)


def _resolution(data: dict[str, Any]) -> LocalIntervalResolution:
    return LocalIntervalResolution.model_validate(data)


def _lesson_end(start: datetime, duration_minutes: int) -> datetime:
    try:
        end = start + timedelta(minutes=duration_minutes)
    except OverflowError as exc:
        raise ValueError("Lesson duration is outside the supported range.") from exc
    if end <= start:
        raise ValueError("Lesson duration must be positive.")
    return end


def build_lesson_dialog() -> Dialog:
    return Dialog(
        Window(
            Format(
                "Choose a student or book a {personal_label}.\n"
                "Current assignee: {subject_label}.\n{picker_page}\n{error}"
            ),
            Column(
                Select(
                    Format("{item[0]}"),
                    id="student",
                    item_id_getter=lambda item: item[1],
                    items="students",
                    on_click=select_student,
                )
            ),
            Row(
                Button(
                    Const("◀ Previous"),
                    id="previous_students",
                    on_click=previous_students,
                    when="has_previous_students",
                ),
                Button(
                    Const("Next ▶"),
                    id="next_students",
                    on_click=next_students,
                    when="has_next_students",
                ),
            ),
            Button(
                Const("Personal lesson"),
                id="personal",
                on_click=choose_personal_lesson,
            ),
            Button(Const("Back"), id="back", on_click=cancel_subject_picker),
            state=LessonScreens.subject,
        ),
        Window(
            Format("{kind_assignee_text}\n{error}"),
            Button(Const("One time"), id="once", on_click=choose_once),
            Button(Const("Weekly"), id="weekly", on_click=choose_weekly),
            Button(
                Const("Change assignee"),
                id="change_assignee",
                on_click=edit_subject_from_kind,
                when="can_edit_subject",
            ),
            Button(Const("Cancel"), id="cancel", on_click=return_home),
            state=LessonScreens.kind,
        ),
        Window(
            Format(
                "Choose lesson duration. The teacher default is {default_duration} minutes.\n{error}"
            ),
            Button(
                Format("Use default ({default_duration} min)"),
                id="default_duration",
                on_click=use_default_duration,
            ),
            Button(Const("Custom duration"), id="custom", on_click=custom_duration),
            Button(Const("Back"), id="back", on_click=cancel_duration_edit),
            state=LessonScreens.duration,
        ),
        Window(
            Format(
                "Enter the custom duration in minutes. Only teachers can change it.\n{error}"
            ),
            TextInput("duration", on_success=duration_input),
            Button(Const("Back"), id="back", on_click=cancel_duration_edit),
            state=LessonScreens.duration_input,
        ),
        Window(
            Format(
                "{duration_summary}\n"
                "Enter the lesson start date as DD MM, DD:MM or DD/MM, "
                "optionally followed by a year.\n{start_date_hint}\n{error}"
            ),
            TextInput(
                "start_date", on_success=start_date_input, on_error=_date_input_error
            ),
            Button(
                Const("Change duration"),
                id="edit_duration",
                on_click=edit_duration_from_start_date,
                when="can_edit_duration",
            ),
            Button(Const("Cancel"), id="cancel", on_click=return_home),
            state=LessonScreens.start_date,
        ),
        Window(
            Format(
                "{start_date_confirmation}\nConfirm this inferred next-year date.\n{error}"
            ),
            Button(Const("Confirm date"), id="confirm", on_click=confirm_start_date),
            Button(Const("Change date"), id="change", on_click=edit_start_date),
            state=LessonScreens.confirm_start_date,
        ),
        Window(
            Format("Enter the local lesson start time as HH:MM or HH MM.\n{error}"),
            TextInput(
                "start_time", on_success=start_time_input, on_error=_date_input_error
            ),
            Button(Const("Change date"), id="change_date", on_click=edit_start_date),
            Button(Const("Cancel"), id="cancel", on_click=return_home),
            state=LessonScreens.start_time,
        ),
        Window(
            Format(
                "Enter the last lesson start date (inclusive) as DD MM, DD:MM or DD/MM, optionally followed by a year.\n{start_date_hint}\n{error}"
            ),
            TextInput(
                "cutoff_date", on_success=cutoff_date_input, on_error=_date_input_error
            ),
            Button(
                Const("Change start time"), id="change_start", on_click=edit_start_time
            ),
            Button(Const("Cancel"), id="cancel", on_click=return_home),
            state=LessonScreens.cutoff_date,
        ),
        Window(
            Format(
                "{cutoff_date_confirmation}\nConfirm the inferred end date. The series cutoff is the following local midnight.\n{error}"
            ),
            Button(Const("Confirm date"), id="confirm", on_click=confirm_cutoff_date),
            Button(Const("Change date"), id="change", on_click=edit_cutoff_date),
            state=LessonScreens.confirm_cutoff_date,
        ),
        Window(
            Format("Review lesson for {subject_label}\n{review}\n{error}"),
            Button(
                Const("Create lesson"),
                id="submit",
                on_click=submit_lesson,
                when="can_submit",
            ),
            Button(
                Const("Change date"),
                id="edit_date",
                on_click=edit_start_date,
                when="can_edit",
            ),
            Button(
                Const("Change time"),
                id="edit_time",
                on_click=edit_start_time,
                when="can_edit",
            ),
            Button(
                Const("Change duration"),
                id="edit_duration",
                on_click=edit_duration,
                when="can_edit_duration",
            ),
            Button(
                Const("Change assignee"),
                id="edit_subject",
                on_click=edit_subject,
                when="can_edit_subject",
            ),
            Button(
                Const("Change series end"),
                id="edit_cutoff",
                on_click=edit_cutoff_date,
                when="can_edit_cutoff",
            ),
            Button(
                Const("Reload schedule"),
                id="reload",
                on_click=reload_schedule_after_write,
                when="can_reload",
            ),
            Button(Const("Cancel"), id="cancel", on_click=return_home),
            state=LessonScreens.review,
        ),
        Window(
            Format("{error}\nReturn home and retry after the backend is available."),
            Button(Const("Back to home"), id="home", on_click=return_home),
            state=LessonScreens.unavailable,
        ),
        getter=lesson_data,
        on_start=lesson_started,
    )

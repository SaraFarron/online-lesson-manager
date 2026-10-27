from datetime import UTC, date, datetime, time, timedelta
from html import escape
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, ShowMode, StartMode, Window
from aiogram_dialog.widgets.input import ManagedTextInput, TextInput
from aiogram_dialog.widgets.kbd import Button, Row, Select
from aiogram_dialog.widgets.text import Const, Format
from pydantic import ValidationError

from telegram.api import (
    BackendError,
    BackendOutcomeUnknown,
    BackendUnavailable,
    EventCreatePayload,
    EventManagementDetail,
    EventManagementItem,
    EventUpdatePayload,
    EventWriteResult,
    SessionView,
)
from telegram.date_cleanup import delete_accepted_date_message
from telegram.inputs import (
    ParsedScheduleDate,
    combine_schedule_date_time,
    format_human_date,
    format_human_datetime,
    format_human_datetime_in_timezone,
    format_schedule_date_confirmation,
    parse_schedule_date,
    parse_schedule_time,
)
from telegram.onboarding import backend

VACATION_PAGE_SIZE = 20
STUDENT_PAGE_SIZE = 20


class VacationScreens(StatesGroup):
    menu = State()
    subject = State()
    events = State()
    detail = State()
    start_date = State()
    confirm_date = State()
    start_time = State()
    end_date = State()
    review = State()
    confirm_delete = State()


async def open_vacation_management(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Vacation management requires a private chat")
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role not in {"TEACHER", "STUDENT"}:
        raise ValueError("This account cannot manage vacations")
    await manager.start(
        VacationScreens.menu,
        data={
            "view": view.model_dump(mode="json"),
            "viewer_timezone": view.account.timezone,
            "account_id": str(view.account.id),
            "is_teacher": view.account.role == "TEACHER",
            "skip": 0,
            "items": [],
            "total": 0,
            "detail": None,
            "error": "",
            "notice": "",
            "form_action": None,
            "start_date": None,
            "start_time": None,
            "end_date": None,
            "resolution": None,
            "needs_reload": False,
            "outcome_unknown": False,
            "submitting": False,
            "picker_skip": 0,
            "picker_items": [],
        },
        mode=StartMode.NORMAL,
        show_mode=ShowMode.EDIT,
    )


async def vacation_started(start_data: Any, manager: DialogManager) -> None:
    manager.dialog_data.clear()
    manager.dialog_data.update(start_data)


def _detail(manager: DialogManager) -> EventManagementDetail:
    return EventManagementDetail.model_validate(manager.dialog_data["detail"])


def _format_datetime(value: datetime, timezone: str) -> str:
    return format_human_datetime_in_timezone(
        value,
        timezone,
        reference_date=datetime.now(ZoneInfo(timezone)).date(),
    )


def _inclusive_end_date(value: datetime, timezone: str) -> date:
    last_included_instant = value.astimezone(UTC) - timedelta(microseconds=1)
    return last_included_instant.astimezone(ZoneInfo(timezone)).date()


def _vacation_label(item: EventManagementItem, timezone: str) -> str:
    inclusive_end = _inclusive_end_date(item.end, timezone)
    return (
        f"{item.subject_label} · {_format_datetime(item.start, timezone)} "
        f"through {format_human_date(inclusive_end, reference_date=datetime.now(ZoneInfo(timezone)).date())}"
    )


def _parse_local_date(manager: DialogManager, value: str) -> ParsedScheduleDate:
    timezone = ZoneInfo(manager.dialog_data["viewer_timezone"])
    return parse_schedule_date(value, datetime.now(timezone).date())


async def _load_page(
    callback: CallbackQuery, manager: DialogManager, skip: int
) -> None:
    if callback.message is None:
        raise ValueError("Vacation management requires a private chat")
    data = manager.dialog_data
    skip = max(skip, 0)
    try:
        page = await backend(manager).read_management_page(
            callback.from_user.id,
            callback.message.chat.id,
            "vacations",
            skip=skip,
            limit=VACATION_PAGE_SIZE,
        )
    except (BackendError, BackendUnavailable) as exc:
        data.update(skip=skip, items=[], total=0, error=str(exc))
    else:
        data["outcome_unknown"] = False
        data.update(
            skip=skip,
            items=[item.model_dump(mode="json") for item in page.items],
            total=page.total,
            error="",
        )
    await manager.switch_to(VacationScreens.events, show_mode=ShowMode.EDIT)


async def open_vacation_list(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(callback, manager, 0)


async def begin_create(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    data.update(
        form_action="create",
        start_date=None,
        start_time=None,
        end_date=None,
        resolution=None,
        error="",
        notice="",
        needs_reload=False,
        outcome_unknown=False,
        submitting=False,
        subject_id=None,
        subject_label=None,
    )
    if data["is_teacher"]:
        await _load_student_page(_callback, manager, 0)
    else:
        data.update(
            subject_id=data["account_id"],
            subject_label="My vacation",
            edit_flow="create",
        )
        await manager.switch_to(VacationScreens.start_date, show_mode=ShowMode.EDIT)


async def _load_student_page(
    callback: CallbackQuery, manager: DialogManager, skip: int
) -> None:
    if callback.message is None:
        raise ValueError("Vacation subject selection requires a private chat")
    data = manager.dialog_data
    try:
        page = await backend(manager).read_student_picker(
            callback.from_user.id,
            callback.message.chat.id,
            skip=skip,
            limit=STUDENT_PAGE_SIZE,
        )
    except (BackendError, BackendUnavailable) as exc:
        data.update(picker_items=[], error=str(exc))
    else:
        data.update(
            picker_skip=skip,
            picker_items=[student.model_dump(mode="json") for student in page.items],
            picker_total=page.total,
            error="",
        )
    await manager.switch_to(VacationScreens.subject, show_mode=ShowMode.EDIT)


async def select_vacation_subject(
    _callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
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
        edit_flow="create",
        error="",
    )
    await manager.switch_to(VacationScreens.start_date, show_mode=ShowMode.EDIT)


async def choose_personal_vacation(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(
        subject_id=manager.dialog_data["account_id"],
        subject_label="Personal vacation",
        edit_flow="create",
        error="",
    )
    await manager.switch_to(VacationScreens.start_date, show_mode=ShowMode.EDIT)


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
    skip = max(0, int(manager.dialog_data.get("picker_skip", 0)) + offset)
    await _load_student_page(callback, manager, skip)


async def previous_vacations(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(
        callback,
        manager,
        max(0, int(manager.dialog_data["skip"]) - VACATION_PAGE_SIZE),
    )


async def next_vacations(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(
        callback,
        manager,
        int(manager.dialog_data["skip"]) + VACATION_PAGE_SIZE,
    )


async def refresh_vacations(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(callback, manager, int(manager.dialog_data["skip"]))


async def select_vacation(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Vacation management requires a private chat")
    selected = next(
        (
            entry
            for entry in manager.dialog_data.get("items", [])
            if str(entry["id"]) == item
        ),
        None,
    )
    if selected is None:
        manager.dialog_data["error"] = "Reload the list and select a current vacation."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    try:
        detail = await backend(manager).read_management_detail(
            callback.from_user.id,
            callback.message.chat.id,
            "vacations",
            UUID(item),
        )
    except (BackendError, BackendUnavailable) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(
        detail=detail.model_dump(mode="json"),
        notice="",
        error="",
        needs_reload=False,
        outcome_unknown=False,
        operation=None,
    )
    await manager.switch_to(VacationScreens.detail, show_mode=ShowMode.EDIT)


async def begin_edit(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    detail = _detail(manager)
    timezone = ZoneInfo(manager.dialog_data["viewer_timezone"])
    start = detail.start.astimezone(timezone)
    manager.dialog_data.update(
        form_action="edit",
        edit_flow="edit",
        subject_id=str(detail.user_id),
        subject_label=detail.subject_label,
        start_date=start.date().isoformat(),
        start_time=start.strftime("%H:%M"),
        end_date=_inclusive_end_date(
            detail.end, manager.dialog_data["viewer_timezone"]
        ).isoformat(),
        resolution=None,
        error="",
        notice="",
        needs_reload=False,
        outcome_unknown=False,
    )
    await manager.switch_to(VacationScreens.review, show_mode=ShowMode.EDIT)


async def _begin_date_input(manager: DialogManager, field: str) -> None:
    manager.dialog_data.update(date_field=field, error="")
    state = (
        VacationScreens.start_date
        if field == "start_date"
        else VacationScreens.end_date
    )
    await manager.switch_to(state, show_mode=ShowMode.EDIT)


async def start_date_input(
    message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    await _date_input(
        message,
        manager,
        value,
        str(manager.dialog_data.get("date_field", "start_date")),
    )


async def end_date_input(
    message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    await _date_input(message, manager, value, "end_date")


async def _date_input(
    message: Message, manager: DialogManager, value: str, field: str
) -> None:
    try:
        parsed = _parse_local_date(manager, value)
        other_date = manager.dialog_data.get(
            "end_date" if field == "start_date" else "start_date"
        )
        if other_date is not None:
            existing_date = date.fromisoformat(other_date)
            if field == "start_date" and parsed.value > existing_date:
                raise ValueError(
                    "The vacation start date cannot be after its end date."
                )
            if field == "end_date" and parsed.value < existing_date:
                raise ValueError(
                    "The last vacation date cannot be before its start date."
                )
    except ValueError as exc:
        manager.dialog_data["error"] = str(exc)
        manager.show_mode = ShowMode.EDIT
        return
    manager.dialog_data.update(
        pending_date=parsed.value.isoformat(),
        date_field=field,
        date_confirmation=format_schedule_date_confirmation(parsed),
        error="",
    )
    if parsed.requires_confirmation:
        await delete_accepted_date_message(message, manager)
        await manager.switch_to(VacationScreens.confirm_date, show_mode=ShowMode.EDIT)
    else:
        await _accept_date(manager, message)


async def date_input_error(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    error: ValueError,
) -> None:
    manager.dialog_data["error"] = str(error)
    manager.show_mode = ShowMode.EDIT


async def confirm_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _accept_date(manager)


async def change_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    field = manager.dialog_data.get("date_field", "start_date")
    await _begin_date_input(manager, field)


async def _accept_date(manager: DialogManager, message: Message | None = None) -> None:
    field = manager.dialog_data["date_field"]
    manager.dialog_data.update(
        {field: manager.dialog_data["pending_date"], "error": ""}
    )
    if message is not None:
        await delete_accepted_date_message(message, manager)
    if field == "start_date" and manager.dialog_data.get("edit_flow") == "create":
        await manager.switch_to(VacationScreens.start_time, show_mode=ShowMode.EDIT)
    else:
        await manager.switch_to(VacationScreens.review, show_mode=ShowMode.EDIT)


async def start_time_input(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    try:
        parsed = parse_schedule_time(value)
    except ValueError as exc:
        manager.dialog_data["error"] = str(exc)
        manager.show_mode = ShowMode.EDIT
        return
    manager.dialog_data.update(start_time=parsed, error="")
    if manager.dialog_data.get("edit_flow") == "create":
        await manager.switch_to(VacationScreens.end_date, show_mode=ShowMode.EDIT)
    else:
        await manager.switch_to(VacationScreens.review, show_mode=ShowMode.EDIT)


async def start_time_error(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    error: ValueError,
) -> None:
    manager.dialog_data["error"] = str(error)
    manager.show_mode = ShowMode.EDIT


async def edit_start_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _begin_date_input(manager, "start_date")


async def edit_start_time(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["error"] = ""
    await manager.switch_to(VacationScreens.start_time, show_mode=ShowMode.EDIT)


async def edit_end_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _begin_date_input(manager, "end_date")


def _local_interval(data: dict[str, Any]) -> tuple[datetime, datetime]:
    start_date = date.fromisoformat(data["start_date"])
    end_date = date.fromisoformat(data["end_date"])
    if end_date < start_date:
        raise ValueError("The last vacation date cannot be before the start date.")
    try:
        exclusive_end = end_date + timedelta(days=1)
    except OverflowError as exc:
        raise ValueError(
            "The last vacation date is outside the supported range."
        ) from exc
    start = combine_schedule_date_time(start_date, data["start_time"])
    end = datetime.combine(exclusive_end, time.min)
    if start >= end:
        raise ValueError("The vacation start must be before its exclusive end.")
    return start, end


async def review_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    try:
        start, end = _local_interval(data)
        interval = (
            f"Timezone: {data['viewer_timezone']}\n"
            f"Starts: {format_human_datetime(start, reference_date=datetime.now(ZoneInfo(data['viewer_timezone'])).date())}\n"
            f"Last vacation date (inclusive): "
            f"{format_human_date(date.fromisoformat(data['end_date']), reference_date=datetime.now(ZoneInfo(data['viewer_timezone'])).date())}\n"
            "The backend end is midnight after the last vacation date."
        )
        valid = True
    except (KeyError, TypeError, ValueError) as exc:
        interval = str(exc)
        valid = False
    return {
        "subject_label": escape(
            str(data.get("subject_label") or "the selected person")
        ),
        "review": escape(interval),
        "error": escape(str(data.get("error", ""))),
        "notice": escape(str(data.get("notice", ""))),
        "is_create": data.get("form_action") == "create",
        "can_submit": (
            valid
            and not data.get("submitting")
            and not data.get("needs_reload")
            and not data.get("outcome_unknown")
        ),
        "can_edit": not data.get("submitting")
        and not data.get("needs_reload")
        and not data.get("outcome_unknown"),
    }


async def submit_vacation(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Vacation management requires a private chat")
    data = manager.dialog_data
    if (
        data.get("submitting")
        or data.get("needs_reload")
        or data.get("outcome_unknown")
    ):
        return
    try:
        local_start, local_end = _local_interval(data)
        resolution = await backend(manager).resolve_local_interval(
            callback.from_user.id,
            callback.message.chat.id,
            local_start,
            local_end,
        )
        user_id = UUID(data["subject_id"])
        payload: EventCreatePayload | EventUpdatePayload
        method: Literal["POST", "PUT"]
        if data["form_action"] == "create":
            payload = EventCreatePayload(
                start=resolution.start.utc,
                end=resolution.end.utc,
                user_id=user_id,
                event_type="VACATION",
            )
            method = "POST"
            event_id = None
            revision = None
        else:
            detail = _detail(manager)
            payload = EventUpdatePayload(
                start=resolution.start.utc,
                end=resolution.end.utc,
                user_id=user_id,
                event_type="VACATION",
            )
            method = "PUT"
            event_id = detail.id
            revision = detail.revision
    except (BackendError, BackendUnavailable, ValueError, ValidationError) as exc:
        data.update(error=str(exc), submitting=False)
        await manager.show(show_mode=ShowMode.EDIT)
        return

    data.update(
        submitting=True, error="", resolution=resolution.model_dump(mode="json")
    )
    try:
        await backend(manager).write_management_event(
            method,
            "vacations",
            callback.from_user.id,
            callback.message.chat.id,
            payload,
            EventWriteResult,
            event_id=event_id,
            revision=revision,
        )
    except BackendOutcomeUnknown:
        data.update(
            submitting=False,
            outcome_unknown=True,
            notice=(
                "The save result is unknown. Current vacations are reloaded; "
                "the write will not be repeated."
            ),
            error="",
        )
        await _load_page(callback, manager, int(data.get("skip", 0)))
        return
    except BackendError as exc:
        stale = exc.code == "stale_event_revision" or exc.status == 409
        data.update(
            submitting=False,
            needs_reload=stale,
            error=str(exc),
            notice="",
        )
        if stale:
            data["notice"] = (
                "The vacation changed elsewhere. Current details were reloaded; "
                "review them before saving again."
            )
            await _reload_detail(callback, manager)
        else:
            await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        data.update(submitting=False, error=str(exc))
        await manager.show(show_mode=ShowMode.EDIT)
        return

    data.update(
        submitting=False,
        outcome_unknown=False,
        needs_reload=False,
        notice="Vacation updated." if method == "PUT" else "Vacation created.",
        error="",
    )
    if method == "PUT":
        await _reload_detail(callback, manager)
    else:
        await _load_page(callback, manager, int(data.get("skip", 0)))


async def begin_delete(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(
        error="",
        notice="",
        needs_reload=False,
        outcome_unknown=False,
    )
    await manager.switch_to(VacationScreens.confirm_delete, show_mode=ShowMode.EDIT)


async def confirm_delete(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Vacation management requires a private chat")
    data = manager.dialog_data
    if (
        data.get("submitting")
        or data.get("needs_reload")
        or data.get("outcome_unknown")
    ):
        return
    detail = _detail(manager)
    data.update(submitting=True, error="", notice="")
    try:
        await backend(manager).delete_management_event(
            "vacations",
            detail.id,
            callback.from_user.id,
            callback.message.chat.id,
            revision=detail.revision,
        )
    except BackendOutcomeUnknown:
        data.update(
            submitting=False,
            outcome_unknown=True,
            notice=(
                "The delete result is unknown. Current vacations are reloaded; "
                "the delete will not be repeated."
            ),
        )
        await _load_page(callback, manager, int(data.get("skip", 0)))
        return
    except BackendError as exc:
        stale = exc.code == "stale_event_revision" or exc.status == 409
        data.update(
            submitting=False,
            needs_reload=stale,
            error=str(exc),
        )
        if stale:
            data["notice"] = (
                "The vacation changed elsewhere. Current details were reloaded; "
                "review them before deleting again."
            )
            await _reload_detail(callback, manager)
        else:
            await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        data.update(submitting=False, error=str(exc))
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data.update(
        submitting=False,
        needs_reload=False,
        outcome_unknown=False,
        notice=(
            "Vacation deleted. Existing lesson rows are unchanged; events that "
            "still exist are visible again."
        ),
        error="",
    )
    await _load_page(callback, manager, int(data.get("skip", 0)))


async def _reload_detail(callback: CallbackQuery, manager: DialogManager) -> None:
    if callback.message is None:
        raise ValueError("Vacation management requires a private chat")
    data = manager.dialog_data
    old_detail = _detail(manager)
    try:
        detail = await backend(manager).read_management_detail(
            callback.from_user.id,
            callback.message.chat.id,
            "vacations",
            old_detail.id,
        )
    except (BackendError, BackendUnavailable) as exc:
        data.update(error=str(exc), needs_reload=True)
        await manager.switch_to(VacationScreens.detail, show_mode=ShowMode.EDIT)
        return
    data.update(
        detail=detail.model_dump(mode="json"),
        needs_reload=False,
        outcome_unknown=False,
        error="",
    )
    await manager.switch_to(VacationScreens.detail, show_mode=ShowMode.EDIT)


async def reload_vacations(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        await _load_page(callback, manager, int(manager.dialog_data.get("skip", 0)))
        return
    await _reload_detail(callback, manager)


async def back_to_menu(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.switch_to(VacationScreens.menu, show_mode=ShowMode.EDIT)


async def back_to_list(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(callback, manager, int(manager.dialog_data.get("skip", 0)))


async def cancel_delete(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.switch_to(VacationScreens.detail, show_mode=ShowMode.EDIT)


async def return_home(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.done(show_mode=ShowMode.EDIT)


async def menu_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    return {
        "error": escape(str(dialog_manager.dialog_data.get("error", ""))),
        "notice": escape(str(dialog_manager.dialog_data.get("notice", ""))),
    }


async def list_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    items = [EventManagementItem.model_validate(item) for item in data.get("items", [])]
    skip = int(data.get("skip", 0))
    total = int(data.get("total", 0))
    return {
        "events": [
            (_vacation_label(item, data["viewer_timezone"]), str(item.id))
            for item in items
        ],
        "page": f"Vacations {skip + 1}–{min(skip + len(items), total)} of {total}"
        if total
        else "No vacations.",
        "timezone": escape(data["viewer_timezone"]),
        "error": escape(str(data.get("error", ""))),
        "notice": escape(str(data.get("notice", ""))),
        "has_previous": skip > 0,
        "has_next": skip + len(items) < total,
        "can_reload": bool(data.get("outcome_unknown")),
    }


async def subject_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    skip = int(data.get("picker_skip", 0))
    items = data.get("picker_items", [])
    total = int(data.get("picker_total", len(items)))
    return {
        "students": [
            (escape(str(item["display_label"])), str(item["id"])) for item in items
        ],
        "page": (
            f"Students {skip + 1}–{min(skip + len(items), total)} of {total}"
            if total
            else "No students are linked to this teacher."
        ),
        "error": escape(str(data.get("error", ""))),
        "has_previous": skip > 0,
        "has_next": skip + len(items) < total,
    }


async def detail_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    detail = EventManagementDetail.model_validate(data["detail"])
    timezone = data["viewer_timezone"]
    inclusive_end = _inclusive_end_date(detail.end, timezone)
    return {
        "detail_text": escape(
            f"{detail.subject_label}\n"
            f"Starts: {_format_datetime(detail.start, timezone)}\n"
            f"Last vacation date (inclusive): "
            f"{format_human_date(inclusive_end, reference_date=datetime.now(ZoneInfo(timezone)).date())}\n"
            f"Timezone: {timezone}\n"
            "This vacation hides overlapping schedule events without deleting them."
        ),
        "error": escape(str(data.get("error", ""))),
        "notice": escape(str(data.get("notice", ""))),
        "can_manage": not data.get("needs_reload") and not data.get("outcome_unknown"),
        "can_reload": bool(data.get("needs_reload") or data.get("outcome_unknown")),
    }


async def delete_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    return {
        "warning": (
            "Delete this vacation? Existing events that still exist will show again. "
            "Lessons deleted or cancelled by an earlier confirmed break will not "
            "be restored."
        ),
        "error": escape(str(dialog_manager.dialog_data.get("error", ""))),
        "can_confirm": not dialog_manager.dialog_data.get("submitting")
        and not dialog_manager.dialog_data.get("needs_reload")
        and not dialog_manager.dialog_data.get("outcome_unknown"),
    }


async def date_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    field = dialog_manager.dialog_data.get("date_field", "start_date")
    prompt = (
        "Enter the local start date"
        if field == "start_date"
        else "Enter the last vacation date (inclusive)"
    )
    return {
        "prompt": f"{prompt} as DD MM, DD:MM or DD/MM, optionally with a year.",
        "timezone": escape(dialog_manager.dialog_data["viewer_timezone"]),
        "error": escape(str(dialog_manager.dialog_data.get("error", ""))),
    }


async def timezone_error_data(
    dialog_manager: DialogManager, **_: Any
) -> dict[str, Any]:
    return {
        "timezone": escape(dialog_manager.dialog_data["viewer_timezone"]),
        "error": escape(str(dialog_manager.dialog_data.get("error", ""))),
    }


async def confirmation_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    return {
        "confirmation": escape(
            str(dialog_manager.dialog_data.get("date_confirmation", ""))
        ),
        "error": escape(str(dialog_manager.dialog_data.get("error", ""))),
    }


def build_vacation_dialog() -> Dialog:
    return Dialog(
        Window(
            Const("Vacations hide overlapping events but do not delete them."),
            Button(Const("Vacation list"), id="list", on_click=open_vacation_list),
            Button(Const("Add vacation"), id="add", on_click=begin_create),
            Button(Const("Back to home"), id="home", on_click=return_home),
            state=VacationScreens.menu,
            getter=menu_data,
        ),
        Window(
            Format("{page}\n{error}"),
            Select(
                Format("{item[0]}"),
                id="student",
                item_id_getter=lambda item: item[1],
                items="students",
                on_click=select_vacation_subject,
            ),
            Button(
                Const("Personal vacation"),
                id="personal",
                on_click=choose_personal_vacation,
            ),
            Row(
                Button(
                    Const("◀ Previous"),
                    id="previous",
                    on_click=previous_students,
                    when="has_previous",
                ),
                Button(
                    Const("Next ▶"),
                    id="next",
                    on_click=next_students,
                    when="has_next",
                ),
            ),
            Button(Const("Back"), id="back", on_click=back_to_menu),
            state=VacationScreens.subject,
            getter=subject_data,
        ),
        Window(
            Format("{page}\n{notice}\n{error}\nTimezone: {timezone}"),
            Select(
                Format("{item[0]}"),
                id="vacation",
                item_id_getter=lambda item: item[1],
                items="events",
                on_click=select_vacation,
            ),
            Row(
                Button(
                    Const("◀ Previous"),
                    id="previous",
                    on_click=previous_vacations,
                    when="has_previous",
                ),
                Button(
                    Const("Next ▶"),
                    id="next",
                    on_click=next_vacations,
                    when="has_next",
                ),
            ),
            Row(
                Button(Const("Refresh"), id="refresh", on_click=refresh_vacations),
                Button(Const("Add vacation"), id="add", on_click=begin_create),
            ),
            Button(
                Const("Reload vacations"),
                id="reload",
                on_click=reload_vacations,
                when="can_reload",
            ),
            Button(Const("Back"), id="back", on_click=back_to_menu),
            state=VacationScreens.events,
            getter=list_data,
        ),
        Window(
            Format("{detail_text}\n{notice}\n{error}"),
            Button(
                Const("Edit vacation"),
                id="edit",
                on_click=begin_edit,
                when="can_manage",
            ),
            Button(
                Const("Delete vacation"),
                id="delete",
                on_click=begin_delete,
                when="can_manage",
            ),
            Button(
                Const("Reload vacation"),
                id="reload",
                on_click=reload_vacations,
                when="can_reload",
            ),
            Button(Const("Back to list"), id="list", on_click=back_to_list),
            state=VacationScreens.detail,
            getter=detail_data,
        ),
        Window(
            Format("{prompt}\nTimezone: {timezone}\n{error}"),
            TextInput(
                "start_date",
                on_success=start_date_input,
                on_error=date_input_error,
            ),
            Button(Const("Cancel"), id="cancel", on_click=back_to_menu),
            state=VacationScreens.start_date,
            getter=date_data,
        ),
        Window(
            Format("{confirmation}\nConfirm this inferred next-year date.\n{error}"),
            Button(Const("Confirm date"), id="confirm", on_click=confirm_date),
            Button(Const("Change date"), id="change", on_click=change_date),
            state=VacationScreens.confirm_date,
            getter=confirmation_data,
        ),
        Window(
            Format(
                "Enter the local start time as HH:MM or HH MM.\n"
                "Timezone: {timezone}\n{error}"
            ),
            TextInput(
                "start_time",
                on_success=start_time_input,
                on_error=start_time_error,
            ),
            Button(Const("Cancel"), id="cancel", on_click=back_to_menu),
            state=VacationScreens.start_time,
            getter=timezone_error_data,
        ),
        Window(
            Format(
                "{prompt}\n"
                "The vacation ends at local midnight after this date.\n"
                "Timezone: {timezone}\n{error}"
            ),
            TextInput("end_date", on_success=end_date_input, on_error=date_input_error),
            Button(Const("Cancel"), id="cancel", on_click=back_to_menu),
            state=VacationScreens.end_date,
            getter=date_data,
        ),
        Window(
            Format(
                "Review vacation for {subject_label}\n{review}\n"
                "Overlapping events are hidden, not erased.\n{notice}\n{error}"
            ),
            Button(
                Const("Save vacation"),
                id="submit",
                on_click=submit_vacation,
                when="can_submit",
            ),
            Button(
                Const("Change start date"),
                id="edit_date",
                on_click=edit_start_date,
                when="can_edit",
            ),
            Button(
                Const("Change start time"),
                id="edit_time",
                on_click=edit_start_time,
                when="can_edit",
            ),
            Button(
                Const("Change last vacation date"),
                id="edit_end",
                on_click=edit_end_date,
                when="can_edit",
            ),
            Button(
                Const("Reload vacation"),
                id="reload",
                on_click=reload_vacations,
                when="can_reload",
            ),
            Button(Const("Cancel"), id="cancel", on_click=back_to_menu),
            state=VacationScreens.review,
            getter=review_data,
        ),
        Window(
            Format("{warning}\n{error}"),
            Button(
                Const("Confirm delete"),
                id="confirm",
                on_click=confirm_delete,
                when="can_confirm",
            ),
            Button(Const("Cancel"), id="cancel", on_click=cancel_delete),
            state=VacationScreens.confirm_delete,
            getter=delete_data,
        ),
        on_start=vacation_started,
    )

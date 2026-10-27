from datetime import date, datetime, time, timedelta
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
from pydantic import BaseModel, ValidationError

from telegram.api import (
    BackendError,
    BackendOutcomeUnknown,
    BackendUnavailable,
    BreakDisplacementPreview,
    DisplacedOccurrence,
    EventCreatePayload,
    EventManagementDetail,
    EventManagementItem,
    EventUpdatePayload,
    EventWriteResult,
    LocalIntervalResolution,
    ManagementResource,
    RecurrentEventCreatePayload,
    RecurrentEventUpdatePayload,
    RecurrentEventWriteResult,
    SessionView,
)
from telegram.date_cleanup import delete_accepted_date_message
from telegram.inputs import (
    ParsedScheduleDate,
    combine_schedule_date_time,
    format_human_date,
    format_local_interval_in_timezone,
    format_schedule_date_confirmation,
    parse_schedule_date,
    parse_schedule_time,
)
from telegram.onboarding import backend

BREAK_PAGE_SIZE = 20
DISPLACEMENT_PAGE_SIZE = 10
BreakKind = Literal["once", "weekly"]


class BreakScreens(StatesGroup):
    menu = State()
    events = State()
    detail = State()
    start_date = State()
    confirm_start_date = State()
    start_time = State()
    end_time = State()
    cutoff_date = State()
    confirm_cutoff_date = State()
    review = State()
    displacement = State()
    confirm_delete = State()


async def open_break_management(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Break management requires a private chat")
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role != "TEACHER":
        raise ValueError("Only teachers can manage breaks")
    await manager.start(
        BreakScreens.menu,
        data={
            "view": view.model_dump(mode="json"),
            "viewer_timezone": view.account.timezone,
            "kind": None,
            "skip": 0,
            "items": [],
            "total": 0,
            "detail": None,
            "error": "",
            "notice": "",
            "form_action": None,
            "resolution": None,
            "interval_end": None,
            "preview": None,
            "preview_skip": 0,
            "submitting": False,
            "needs_reload": False,
            "outcome_unknown": False,
            "operation": None,
        },
        mode=StartMode.NORMAL,
        show_mode=ShowMode.EDIT,
    )


async def break_started(start_data: Any, manager: DialogManager) -> None:
    manager.dialog_data.clear()
    manager.dialog_data.update(start_data)


def _kind(manager: DialogManager) -> BreakKind:
    value = manager.dialog_data.get("kind")
    if value == "once":
        return "once"
    if value == "weekly":
        return "weekly"
    raise ValueError("Break list type is missing")


def _resource(manager: DialogManager) -> ManagementResource:
    return "breaks" if _kind(manager) == "once" else "recurrent-breaks"


def _detail(manager: DialogManager) -> EventManagementDetail:
    return EventManagementDetail.model_validate(manager.dialog_data["detail"])


def _teacher_id(manager: DialogManager) -> UUID:
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role != "TEACHER":
        raise ValueError("Only teachers can manage breaks")
    return view.account.id


def _parse_local_date(manager: DialogManager, value: str) -> ParsedScheduleDate:
    timezone = ZoneInfo(manager.dialog_data["viewer_timezone"])
    return parse_schedule_date(value, datetime.now(timezone).date())


def _format_interval(start: datetime, end: datetime, timezone: str) -> str:
    return format_local_interval_in_timezone(
        start,
        end,
        timezone,
        reference_date=datetime.now(ZoneInfo(timezone)).date(),
    )


def _format_date(value: date, timezone: str) -> str:
    return format_human_date(
        value, reference_date=datetime.now(ZoneInfo(timezone)).date()
    )


def _break_label(item: EventManagementItem, timezone: str) -> str:
    label = "Weekly break" if item.is_recurrent else "One-time break"
    return f"{label} · {_format_interval(item.start, item.end, timezone)}"


async def _load_page(
    callback: CallbackQuery, manager: DialogManager, kind: BreakKind, skip: int
) -> None:
    if callback.message is None:
        raise ValueError("Break management requires a private chat")
    try:
        page = await backend(manager).read_management_page(
            callback.from_user.id,
            callback.message.chat.id,
            "breaks" if kind == "once" else "recurrent-breaks",
            skip=skip,
            limit=BREAK_PAGE_SIZE,
        )
    except (BackendError, BackendUnavailable) as exc:
        manager.dialog_data.update(
            kind=kind, skip=skip, items=[], total=0, error=str(exc)
        )
    else:
        manager.dialog_data.update(
            kind=kind,
            skip=skip,
            items=[item.model_dump(mode="json") for item in page.items],
            total=page.total,
            error="",
        )
    await manager.switch_to(BreakScreens.events, show_mode=ShowMode.EDIT)


async def choose_one_time(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(callback, manager, "once", 0)


async def choose_weekly(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(callback, manager, "weekly", 0)


async def previous_events(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(
        callback,
        manager,
        _kind(manager),
        max(0, int(manager.dialog_data["skip"]) - BREAK_PAGE_SIZE),
    )


async def next_events(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(
        callback,
        manager,
        _kind(manager),
        int(manager.dialog_data["skip"]) + BREAK_PAGE_SIZE,
    )


async def refresh_events(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(
        callback, manager, _kind(manager), int(manager.dialog_data["skip"])
    )


async def select_break(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Break management requires a private chat")
    selected = next(
        (
            entry
            for entry in manager.dialog_data.get("items", [])
            if str(entry["id"]) == item
        ),
        None,
    )
    if selected is None:
        manager.dialog_data["error"] = "Reload the list and select a current break."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    try:
        detail = await backend(manager).read_management_detail(
            callback.from_user.id,
            callback.message.chat.id,
            _resource(manager),
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
        preview=None,
    )
    await manager.switch_to(BreakScreens.detail, show_mode=ShowMode.EDIT)


async def begin_create(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(
        form_action="create",
        start_date=None,
        start_time=None,
        resolution=None,
        cutoff_date=None,
        interval_end=None,
        error="",
        notice="",
        preview=None,
        needs_reload=False,
        outcome_unknown=False,
    )
    await manager.switch_to(BreakScreens.start_date, show_mode=ShowMode.EDIT)


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
        start_date_confirmation=(
            format_schedule_date_confirmation(parsed)
            if parsed.requires_confirmation
            else ""
        ),
        error="",
    )
    await delete_accepted_date_message(message, manager)
    await manager.switch_to(
        BreakScreens.confirm_start_date
        if parsed.requires_confirmation
        else BreakScreens.start_time,
        show_mode=ShowMode.EDIT,
    )


async def confirm_start_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.switch_to(BreakScreens.start_time, show_mode=ShowMode.EDIT)


async def change_start_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(
        start_date=None,
        start_time=None,
        resolution=None,
        cutoff_date=None,
        interval_end=None,
        error="",
    )
    await manager.switch_to(BreakScreens.start_date, show_mode=ShowMode.EDIT)


async def change_start_time(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(start_time=None, resolution=None, error="")
    await manager.switch_to(BreakScreens.start_time, show_mode=ShowMode.EDIT)


async def change_end_time(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(
        resolution=None, cutoff_date=None, interval_end=None, error=""
    )
    await manager.switch_to(BreakScreens.end_time, show_mode=ShowMode.EDIT)


async def change_cutoff_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(cutoff_date=None, interval_end=None, error="")
    await manager.switch_to(BreakScreens.cutoff_date, show_mode=ShowMode.EDIT)


async def start_time_input(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    try:
        start_time = parse_schedule_time(value)
        manager.dialog_data.update(
            start_time=start_time,
            error="",
        )
    except (OverflowError, ValueError) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    await manager.switch_to(BreakScreens.end_time, show_mode=ShowMode.EDIT)


async def end_time_input(
    message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    if message.from_user is None:
        raise ValueError("Break management requires a private chat")
    try:
        end_time = parse_schedule_time(value)
        local_date = date.fromisoformat(manager.dialog_data["start_date"])
        local_start = combine_schedule_date_time(
            local_date, manager.dialog_data["start_time"]
        )
        local_end = combine_schedule_date_time(local_date, end_time)
        if local_end <= local_start:
            raise ValueError("The break end time must be after its start time.")
        resolution = await backend(manager).resolve_local_interval(
            message.from_user.id,
            message.chat.id,
            local_start,
            local_end,
        )
    except (BackendError, BackendUnavailable, OverflowError, ValueError) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(
        resolution=resolution.model_dump(mode="json"),
        error="",
    )
    if _kind(manager) == "weekly":
        manager.dialog_data.update(
            cutoff_date=None,
            interval_end=None,
            cutoff_date_confirmation="",
        )
        await manager.switch_to(BreakScreens.cutoff_date, show_mode=ShowMode.EDIT)
    else:
        await manager.switch_to(BreakScreens.review, show_mode=ShowMode.EDIT)


async def cutoff_date_input(
    message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    if message.from_user is None:
        raise ValueError("Break management requires a private chat")
    try:
        parsed = _parse_local_date(manager, value)
        if parsed.value < date.fromisoformat(manager.dialog_data["start_date"]):
            raise ValueError("The series end date cannot be before its first break.")
    except (OverflowError, ValueError) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(
        cutoff_date=parsed.value.isoformat(),
        cutoff_date_confirmation=(
            format_schedule_date_confirmation(parsed)
            if parsed.requires_confirmation
            else ""
        ),
        error="",
    )
    await delete_accepted_date_message(message, manager)
    if parsed.requires_confirmation:
        await manager.switch_to(
            BreakScreens.confirm_cutoff_date, show_mode=ShowMode.EDIT
        )
        return
    await _resolve_cutoff_date(message.from_user.id, message.chat.id, manager)


async def confirm_cutoff_date(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Break management requires a private chat")
    await _resolve_cutoff_date(callback.from_user.id, callback.message.chat.id, manager)


async def _resolve_cutoff_date(
    sender_id: int, chat_id: int, manager: DialogManager
) -> None:
    data = manager.dialog_data
    try:
        next_day = date.fromisoformat(data["cutoff_date"]) + timedelta(days=1)
        boundary = datetime.combine(next_day, time.min)
        resolution = await backend(manager).resolve_local_interval(
            sender_id,
            chat_id,
            boundary - timedelta(minutes=1),
            boundary,
        )
        cutoff = resolution.end.utc
        start = datetime.fromisoformat(data["resolution"]["start"]["utc"])
        if cutoff <= start:
            raise ValueError("The series end date must include its first break.")
    except (BackendError, BackendUnavailable, OverflowError, ValueError) as exc:
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data.update(interval_end=cutoff.isoformat(), error="")
    await manager.switch_to(BreakScreens.review, show_mode=ShowMode.EDIT)


async def begin_edit(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(
        form_action="edit",
        start_date=None,
        start_time=None,
        resolution=None,
        cutoff_date=None,
        interval_end=None,
        error="",
        notice="",
        preview=None,
        needs_reload=False,
        outcome_unknown=False,
    )
    await manager.switch_to(BreakScreens.start_date, show_mode=ShowMode.EDIT)


def _review_text(manager: DialogManager) -> str:
    data = manager.dialog_data
    resolution = LocalIntervalResolution.model_validate(data["resolution"])
    action = "Update" if data.get("form_action") == "edit" else "Create"
    text = (
        f"{action} {'weekly' if _kind(manager) == 'weekly' else 'one-time'} break\n"
        f"Break time: {_format_interval(resolution.start.utc, resolution.end.utc, resolution.timezone)}\n"
        f"Timezone: {resolution.timezone}"
    )
    if _kind(manager) == "weekly":
        interval_end = datetime.fromisoformat(data["interval_end"])
        inclusive_end = interval_end.astimezone(
            ZoneInfo(data["viewer_timezone"])
        ).date() - timedelta(days=1)
        text += (
            f"\nLast break date (inclusive): "
            f"{_format_date(inclusive_end, data['viewer_timezone'])}"
        )
    if data.get("form_action") == "edit" and _kind(manager) == "weekly":
        detail = _detail(manager)
        if (
            resolution.start.utc != detail.start
            or resolution.end.utc != detail.end
            or datetime.fromisoformat(data["interval_end"]) != detail.interval_end
        ):
            text += (
                "\nChanging the recurring break's date, time, or cutoff does not "
                "restore lessons previously displaced by this break."
            )
    return text


async def review_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    try:
        review = _review_text(dialog_manager)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        review = f"Review could not be displayed: {exc}"
    blocked = bool(data.get("needs_reload") or data.get("outcome_unknown"))
    return {
        "review": escape(review),
        "error": escape(str(data.get("error", ""))),
        "notice": escape(str(data.get("notice", ""))),
        "save_label": "Save break"
        if data.get("form_action") == "create"
        else "Save change",
        "can_submit": not blocked and not data.get("submitting"),
        "can_edit": not blocked and not data.get("submitting"),
        "can_edit_cutoff": (
            not blocked and not data.get("submitting") and data.get("kind") == "weekly"
        ),
        "can_reload": blocked,
        "can_back_to_detail": data.get("form_action") == "edit",
    }


async def submit_review(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Break management requires a private chat")
    data = manager.dialog_data
    if (
        data.get("submitting")
        or data.get("needs_reload")
        or data.get("outcome_unknown")
    ):
        return
    try:
        payload = _build_payload(manager)
    except (ValidationError, ValueError, OverflowError) as exc:
        data["error"] = f"Review could not be submitted: {exc}"
        await manager.show(show_mode=ShowMode.EDIT)
        return
    await _send_write(callback, manager, payload, confirm_displacement=False)


def _build_payload(manager: DialogManager) -> BaseModel:
    data = manager.dialog_data
    detail = _detail(manager) if data.get("form_action") == "edit" else None
    resolution = LocalIntervalResolution.model_validate(data["resolution"])
    user_id = _teacher_id(manager)
    if _kind(manager) == "once":
        if detail is None:
            return EventCreatePayload(
                start=resolution.start.utc,
                end=resolution.end.utc,
                user_id=user_id,
                event_type="BREAK",
            )
        return EventUpdatePayload(
            start=resolution.start.utc,
            end=resolution.end.utc,
            user_id=user_id,
            event_type="BREAK",
        )
    interval = detail.interval if detail is not None else 7
    if interval is None:
        raise ValueError("The recurring break interval is unavailable.")
    if detail is None:
        return RecurrentEventCreatePayload(
            start=resolution.start.utc,
            end=resolution.end.utc,
            user_id=user_id,
            event_type="BREAK",
            interval=interval,
            interval_end=datetime.fromisoformat(data["interval_end"]),
        )
    return RecurrentEventUpdatePayload(
        start=resolution.start.utc,
        end=resolution.end.utc,
        user_id=user_id,
        event_type="BREAK",
        interval=interval,
        interval_end=datetime.fromisoformat(data["interval_end"]),
    )


def _response_model(manager: DialogManager) -> type[BaseModel]:
    if _kind(manager) == "once":
        return EventWriteResult
    return RecurrentEventWriteResult


async def _send_write(
    callback: CallbackQuery,
    manager: DialogManager,
    payload: BaseModel,
    *,
    confirm_displacement: bool,
    displacement_token: str | None = None,
) -> None:
    if callback.message is None:
        raise ValueError("Break management requires a private chat")
    data = manager.dialog_data
    if data.get("submitting"):
        return
    is_edit = data.get("form_action") == "edit"
    detail = _detail(manager) if is_edit else None
    resource = _resource(manager)
    data.update(submitting=True, error="", notice="")
    try:
        await backend(manager).write_management_event(
            "PUT" if is_edit else "POST",
            resource,
            callback.from_user.id,
            callback.message.chat.id,
            payload,
            _response_model(manager),
            event_id=detail.id if detail is not None else None,
            revision=detail.revision if detail is not None else None,
            confirm_displacement=confirm_displacement,
            displacement_token=displacement_token,
        )
    except BackendError as exc:
        data["submitting"] = False
        if exc.displacement_preview is not None:
            _store_preview(data, payload, exc.displacement_preview)
            await manager.switch_to(BreakScreens.displacement, show_mode=ShowMode.EDIT)
            return
        data.update(
            error=str(exc),
            needs_reload=exc.code == "stale_event_revision",
            operation="update"
            if is_edit and exc.code == "stale_event_revision"
            else None,
            preview=None,
        )
        await manager.switch_to(BreakScreens.review, show_mode=ShowMode.EDIT)
        return
    except BackendOutcomeUnknown as exc:
        data.update(
            submitting=False,
            outcome_unknown=True,
            needs_reload=True,
            operation="update" if is_edit else "create",
            error=str(exc),
            notice="The write outcome is unknown. Reload before taking another action.",
            preview=None,
        )
        await manager.switch_to(BreakScreens.review, show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        data.update(submitting=False, error=str(exc), preview=None)
        await manager.switch_to(BreakScreens.review, show_mode=ShowMode.EDIT)
        return
    except (ValidationError, ValueError, OverflowError) as exc:
        data.update(submitting=False, error=str(exc), preview=None)
        await manager.switch_to(BreakScreens.review, show_mode=ShowMode.EDIT)
        return

    data.update(
        submitting=False,
        outcome_unknown=False,
        needs_reload=False,
        operation=None,
        preview=None,
        notice="Break updated." if is_edit else "Break created.",
        error="",
    )
    if is_edit:
        await _refresh_detail(callback, manager)
    else:
        await _load_page(callback, manager, _kind(manager), int(data["skip"]))


def _store_preview(
    data: dict[str, Any],
    payload: BaseModel,
    preview: BreakDisplacementPreview,
) -> None:
    data.update(
        preview=preview.model_dump(mode="json"),
        pending_payload=payload.model_dump(mode="json"),
        preview_skip=0,
        error="",
        notice="",
        needs_reload=False,
        outcome_unknown=False,
    )


async def previous_preview(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["preview_skip"] = max(
        0, int(manager.dialog_data.get("preview_skip", 0)) - DISPLACEMENT_PAGE_SIZE
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def next_preview(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    preview = BreakDisplacementPreview.model_validate(manager.dialog_data["preview"])
    skip = int(manager.dialog_data.get("preview_skip", 0))
    if skip + DISPLACEMENT_PAGE_SIZE < len(preview.affected_occurrences):
        manager.dialog_data["preview_skip"] = skip + DISPLACEMENT_PAGE_SIZE
    await manager.show(show_mode=ShowMode.EDIT)


def _occurrence_label(occurrence: DisplacedOccurrence, timezone: str) -> str:
    kind = "One-time lesson" if occurrence.source_kind == "event" else "Weekly lesson"
    label = f"{kind} · {_format_interval(occurrence.start, occurrence.end, timezone)}"
    if occurrence.automatic_break is not None:
        label += (
            f"\n  Automatic break reservation (read-only): "
            f"{occurrence.automatic_break.duration_minutes} minutes · "
            f"{_format_interval(occurrence.automatic_break.start, occurrence.automatic_break.end, timezone)}"
        )
    return label


async def displacement_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    preview = BreakDisplacementPreview.model_validate(data["preview"])
    skip = int(data.get("preview_skip", 0))
    items = preview.affected_occurrences[skip : skip + DISPLACEMENT_PAGE_SIZE]
    total = len(preview.affected_occurrences)
    return {
        "affected": "\n".join(
            escape(_occurrence_label(item, data["viewer_timezone"])) for item in items
        ),
        "page": (
            f"Showing affected lessons {skip + 1}–{min(skip + len(items), total)} "
            f"of {total}"
        ),
        "timezone": escape(data["viewer_timezone"]),
        "has_previous": skip > 0,
        "has_next": skip + len(items) < total,
        "warning": (
            "Confirming permanently deletes overlapping one-time lessons and "
            "cancels exact weekly occurrences. Deleting this break later will not "
            "restore them."
        ),
        "error": escape(str(data.get("error", ""))),
        "can_confirm": not data.get("submitting"),
    }


async def confirm_displacement(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    if data.get("submitting") or data.get("outcome_unknown"):
        return
    if callback.message is None:
        raise ValueError("Break management requires a private chat")
    preview = BreakDisplacementPreview.model_validate(data["preview"])
    payload_type: type[BaseModel]
    if _kind(manager) == "once":
        payload_type = (
            EventUpdatePayload
            if data.get("form_action") == "edit"
            else EventCreatePayload
        )
    else:
        payload_type = (
            RecurrentEventUpdatePayload
            if data.get("form_action") == "edit"
            else RecurrentEventCreatePayload
        )
    try:
        payload = payload_type.model_validate(data["pending_payload"])
    except ValidationError as exc:
        operation = "update" if data.get("form_action") == "edit" else "create"
        data.update(error=str(exc), needs_reload=True, operation=operation)
        await manager.switch_to(BreakScreens.review, show_mode=ShowMode.EDIT)
        return
    await _send_write(
        callback,
        manager,
        payload,
        confirm_displacement=True,
        displacement_token=preview.confirmation_token,
    )


async def cancel_displacement(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(
        preview=None,
        pending_payload=None,
        preview_skip=0,
        error="",
        notice="No break changes were made.",
    )
    await manager.switch_to(BreakScreens.review, show_mode=ShowMode.EDIT)


async def begin_delete(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    detail = _detail(manager)
    manager.dialog_data.update(
        error="",
        notice="",
        confirm_text=(
            "Delete this break? Any lessons previously displaced by it will remain "
            "deleted or cancelled."
            if not detail.is_recurrent
            else "Delete the entire recurring break? Any lessons previously "
            "displaced by it will remain deleted or cancelled."
        ),
        operation=None,
        needs_reload=False,
        outcome_unknown=False,
    )
    await manager.switch_to(BreakScreens.confirm_delete, show_mode=ShowMode.EDIT)


async def confirm_delete(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Break management requires a private chat")
    data = manager.dialog_data
    if (
        data.get("submitting")
        or data.get("needs_reload")
        or data.get("outcome_unknown")
    ):
        return
    detail = _detail(manager)
    data.update(submitting=True, error="")
    try:
        await backend(manager).delete_management_event(
            _resource(manager),
            detail.id,
            callback.from_user.id,
            callback.message.chat.id,
            revision=detail.revision,
        )
    except BackendOutcomeUnknown as exc:
        data.update(
            submitting=False,
            outcome_unknown=True,
            needs_reload=True,
            operation="delete",
            error=str(exc),
            notice="The delete outcome is unknown. Reload before taking another action.",
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except (BackendError, BackendUnavailable) as exc:
        data.update(
            submitting=False,
            needs_reload=isinstance(exc, BackendError)
            and exc.code == "stale_event_revision",
            operation=(
                "delete"
                if isinstance(exc, BackendError) and exc.code == "stale_event_revision"
                else None
            ),
            error=str(exc),
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return

    data.update(submitting=False, operation=None, preview=None, error="")
    data["notice"] = "Break deleted. Displaced lessons were not restored."
    await _load_page(callback, manager, _kind(manager), int(data["skip"]))


async def reload_state(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Break management requires a private chat")
    data = manager.dialog_data
    operation = data.get("operation")
    if operation == "create":
        data.update(outcome_unknown=False, needs_reload=False, preview=None)
        await _load_page(callback, manager, _kind(manager), int(data["skip"]))
        manager.dialog_data["notice"] = (
            "The break list was reloaded. Check it before retrying; no write was repeated."
        )
        return
    detail = _detail(manager)
    try:
        refreshed = await backend(manager).read_management_detail(
            callback.from_user.id,
            callback.message.chat.id,
            _resource(manager),
            detail.id,
        )
    except BackendError as exc:
        if operation == "delete" and exc.status in {404, 422}:
            await _load_page(callback, manager, _kind(manager), int(data["skip"]))
            manager.dialog_data.update(
                outcome_unknown=False,
                needs_reload=False,
                notice="The break is no longer available. No write was repeated.",
                error="",
                operation=None,
            )
            return
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data.update(
        detail=refreshed.model_dump(mode="json"),
        needs_reload=False,
        outcome_unknown=False,
        operation=None,
        preview=None,
        error="",
        notice="Current break details were reloaded. No write was repeated.",
    )
    await manager.switch_to(BreakScreens.detail, show_mode=ShowMode.EDIT)


async def _refresh_detail(callback: CallbackQuery, manager: DialogManager) -> None:
    if callback.message is None:
        raise ValueError("Break management requires a private chat")
    data = manager.dialog_data
    try:
        refreshed = await backend(manager).read_management_detail(
            callback.from_user.id,
            callback.message.chat.id,
            _resource(manager),
            _detail(manager).id,
        )
    except (BackendError, BackendUnavailable) as exc:
        data.update(
            needs_reload=True,
            operation="update",
            error=str(exc),
            notice="The break was saved, but fresh details could not be loaded.",
        )
    else:
        data.update(
            detail=refreshed.model_dump(mode="json"),
            needs_reload=False,
            outcome_unknown=False,
            operation=None,
            error="",
        )
    await manager.switch_to(BreakScreens.detail, show_mode=ShowMode.EDIT)


async def back_to_detail(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(error="", notice="")
    await manager.switch_to(BreakScreens.detail, show_mode=ShowMode.EDIT)


async def back_to_list(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(
        callback, manager, _kind(manager), int(manager.dialog_data["skip"])
    )


async def back_to_menu(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.switch_to(BreakScreens.menu, show_mode=ShowMode.EDIT)


async def return_home(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.done(show_mode=ShowMode.EDIT)


async def detail_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    detail = EventManagementDetail.model_validate(data["detail"])
    timezone = data["viewer_timezone"]
    text = (
        f"{'Recurring' if detail.is_recurrent else 'One-time'} break\n"
        f"Break time: {_format_interval(detail.start, detail.end, timezone)}"
    )
    if detail.is_recurrent:
        assert detail.interval is not None
        assert detail.interval_end is not None
        last_day = detail.interval_end.astimezone(
            ZoneInfo(timezone)
        ).date() - timedelta(days=1)
        text += (
            f"\nRepeats every {detail.interval} days"
            f"\nLast break date (inclusive): {_format_date(last_day, timezone)}"
        )
    text += f"\nTimezone: {timezone}"
    blocked = bool(data.get("needs_reload") or data.get("outcome_unknown"))
    return {
        "detail_text": escape(text),
        "error": escape(str(data.get("error", ""))),
        "notice": escape(str(data.get("notice", ""))),
        "can_manage": not blocked,
        "can_reload": blocked,
    }


async def list_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    items = [EventManagementItem.model_validate(item) for item in data.get("items", [])]
    skip = int(data.get("skip", 0))
    total = int(data.get("total", 0))
    return {
        "title": "One-time breaks"
        if data.get("kind") == "once"
        else "Recurring breaks",
        "timezone": escape(data["viewer_timezone"]),
        "breaks": [
            (_break_label(item, data["viewer_timezone"]), str(item.id))
            for item in items
        ],
        "page": (
            f"Showing {skip + 1}–{min(skip + len(items), total)} of {total}"
            if items
            else f"No breaks on this page ({total} total)"
        ),
        "error": escape(str(data.get("error", ""))),
        "notice": escape(str(data.get("notice", ""))),
        "has_previous": skip > 0,
        "has_next": skip + len(items) < total,
        "is_once": data.get("kind") == "once",
        "is_weekly": data.get("kind") == "weekly",
    }


async def management_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    result = await list_data(dialog_manager)
    result.update(
        date_hint=escape(
            f"Enter a local date in {data.get('viewer_timezone', 'your timezone')}."
        ),
        start_date_confirmation=escape(str(data.get("start_date_confirmation", ""))),
        cutoff_date_confirmation=escape(str(data.get("cutoff_date_confirmation", ""))),
        viewer_timezone=data["viewer_timezone"],
        confirm_text=escape(str(data.get("confirm_text", ""))),
    )
    if data.get("detail") is not None:
        result.update(await detail_data(dialog_manager))
    result.update(await review_data(dialog_manager))
    return result


async def break_error(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    error: ValueError,
) -> None:
    manager.dialog_data["error"] = str(error)
    manager.show_mode = ShowMode.EDIT


def build_break_dialog() -> Dialog:
    return Dialog(
        Window(
            Const(
                "Manage teacher breaks. Breaks block availability for all your students."
            ),
            Button(Const("One-time breaks"), id="once", on_click=choose_one_time),
            Button(Const("Recurring breaks"), id="weekly", on_click=choose_weekly),
            Button(Const("Back to home"), id="home", on_click=return_home),
            state=BreakScreens.menu,
        ),
        Window(
            Format("{title}\n{page}\n{notice}\n{error}\nTimezone: {timezone}"),
            Select(
                Format("{item[0]}"),
                id="break",
                item_id_getter=lambda item: item[1],
                items="breaks",
                on_click=select_break,
            ),
            Row(
                Button(
                    Const("◀ Previous"),
                    id="previous",
                    on_click=previous_events,
                    when="has_previous",
                ),
                Button(
                    Const("Next ▶"),
                    id="next",
                    on_click=next_events,
                    when="has_next",
                ),
            ),
            Row(
                Button(Const("Add break"), id="add", on_click=begin_create),
                Button(Const("Refresh"), id="refresh", on_click=refresh_events),
            ),
            Row(
                Button(
                    Const("Recurring breaks"),
                    id="weekly",
                    on_click=choose_weekly,
                    when="is_once",
                ),
                Button(
                    Const("One-time breaks"),
                    id="once",
                    on_click=choose_one_time,
                    when="is_weekly",
                ),
            ),
            Button(Const("Back"), id="back", on_click=back_to_menu),
            state=BreakScreens.events,
        ),
        Window(
            Format("{detail_text}\n{notice}\n{error}"),
            Button(
                Const("Edit break"), id="edit", on_click=begin_edit, when="can_manage"
            ),
            Button(
                Const("Delete break"),
                id="delete",
                on_click=begin_delete,
                when="can_manage",
            ),
            Button(
                Const("Reload break"),
                id="reload",
                on_click=reload_state,
                when="can_reload",
            ),
            Button(Const("Back to list"), id="list", on_click=back_to_list),
            state=BreakScreens.detail,
        ),
        Window(
            Format(
                "Enter the break date as DD MM, DD:MM, or DD/MM, optionally with a year.\n{date_hint}\n{error}"
            ),
            TextInput("start_date", on_success=start_date_input, on_error=break_error),
            Button(Const("Cancel"), id="cancel", on_click=back_to_list),
            state=BreakScreens.start_date,
        ),
        Window(
            Format(
                "{start_date_confirmation}\nConfirm this inferred next-year date.\n{error}"
            ),
            Button(Const("Confirm date"), id="confirm", on_click=confirm_start_date),
            Button(Const("Change date"), id="change", on_click=change_start_date),
            Button(Const("Cancel"), id="cancel", on_click=back_to_list),
            state=BreakScreens.confirm_start_date,
        ),
        Window(
            Format(
                "Enter the break start time as HH:MM or HH MM.\nTimezone: {viewer_timezone}\n{error}"
            ),
            TextInput("start_time", on_success=start_time_input, on_error=break_error),
            Button(Const("Change date"), id="date", on_click=change_start_date),
            Button(Const("Cancel"), id="cancel", on_click=back_to_list),
            state=BreakScreens.start_time,
        ),
        Window(
            Format(
                "Enter the break end time on the same local date.\nTimezone: {viewer_timezone}\n{error}"
            ),
            TextInput("end_time", on_success=end_time_input, on_error=break_error),
            Button(Const("Change start time"), id="start", on_click=change_start_time),
            Button(Const("Cancel"), id="cancel", on_click=back_to_list),
            state=BreakScreens.end_time,
        ),
        Window(
            Format(
                "Enter the last recurring break date (inclusive) as DD MM, DD:MM, or DD/MM, optionally with a year.\n{date_hint}\n{error}"
            ),
            TextInput(
                "cutoff_date", on_success=cutoff_date_input, on_error=break_error
            ),
            Button(Const("Change break time"), id="time", on_click=change_start_time),
            Button(Const("Cancel"), id="cancel", on_click=back_to_list),
            state=BreakScreens.cutoff_date,
        ),
        Window(
            Format(
                "{cutoff_date_confirmation}\nConfirm the inferred end date. The series cutoff is the following local midnight.\n{error}"
            ),
            Button(Const("Confirm date"), id="confirm", on_click=confirm_cutoff_date),
            Button(Const("Change date"), id="change", on_click=change_cutoff_date),
            Button(Const("Cancel"), id="cancel", on_click=back_to_list),
            state=BreakScreens.confirm_cutoff_date,
        ),
        Window(
            Format("{review}\n{notice}\n{error}"),
            Button(
                Format("{save_label}"),
                id="submit",
                on_click=submit_review,
                when="can_submit",
            ),
            Button(
                Const("Reload"),
                id="reload",
                on_click=reload_state,
                when="can_reload",
            ),
            Row(
                Button(
                    Const("Change date"),
                    id="change_date",
                    on_click=change_start_date,
                    when="can_edit",
                ),
                Button(
                    Const("Change start"),
                    id="change_start",
                    on_click=change_start_time,
                    when="can_edit",
                ),
                Button(
                    Const("Change end"),
                    id="change_end",
                    on_click=change_end_time,
                    when="can_edit",
                ),
                Button(
                    Const("Change last date"),
                    id="change_cutoff",
                    on_click=change_cutoff_date,
                    when="can_edit_cutoff",
                ),
            ),
            Button(
                Const("Back to event"),
                id="detail",
                on_click=back_to_detail,
                when="can_back_to_detail",
            ),
            Button(Const("Cancel"), id="cancel", on_click=back_to_list),
            state=BreakScreens.review,
        ),
        Window(
            Format(
                "This break overlaps the following lessons:\n{affected}\n{page}\n{warning}\n{error}\nTimezone: {timezone}"
            ),
            Row(
                Button(
                    Const("◀ Previous"),
                    id="previous",
                    on_click=previous_preview,
                    when="has_previous",
                ),
                Button(
                    Const("Next ▶"),
                    id="next",
                    on_click=next_preview,
                    when="has_next",
                ),
            ),
            Button(
                Const("Confirm and displace lessons"),
                id="confirm",
                on_click=confirm_displacement,
                when="can_confirm",
            ),
            Button(
                Const("Cancel; make no changes"),
                id="cancel",
                on_click=cancel_displacement,
            ),
            state=BreakScreens.displacement,
            getter=displacement_data,
        ),
        Window(
            Format("{confirm_text}\n{notice}\n{error}"),
            Button(
                Const("Confirm delete"),
                id="confirm",
                on_click=confirm_delete,
                when="can_manage",
            ),
            Button(
                Const("Reload break"),
                id="reload",
                on_click=reload_state,
                when="can_reload",
            ),
            Button(Const("Cancel"), id="cancel", on_click=back_to_detail),
            state=BreakScreens.confirm_delete,
        ),
        getter=management_data,
        on_start=break_started,
    )

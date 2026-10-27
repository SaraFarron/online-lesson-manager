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
from pydantic import ValidationError

from telegram.api import (
    BackendError,
    BackendOutcomeUnknown,
    BackendUnavailable,
    EventManagementDetail,
    EventManagementItem,
    EventUpdatePayload,
    EventWriteResult,
    ManagementResource,
    RecurrentEventUpdatePayload,
    RecurrentEventWriteResult,
    RecurrentOccurrenceCancelPayload,
    RecurrentOccurrenceMovePayload,
    SessionView,
)
from telegram.date_cleanup import (
    delete_accepted_date_message,
    delete_accepted_input_message,
)
from telegram.inputs import (
    combine_schedule_date_time,
    format_duration,
    format_human_date,
    format_human_datetime_in_timezone,
    format_local_interval_in_timezone,
    format_schedule_date_confirmation,
    parse_schedule_date,
    parse_schedule_time,
)
from telegram.onboarding import backend

MANAGEMENT_PAGE_SIZE = 20
EventKind = Literal["once", "weekly"]
ManagementAction = Literal["inspect", "edit", "cutoff", "move"]


class LessonManagementScreens(StatesGroup):
    menu = State()
    events = State()
    detail = State()
    date = State()
    confirm_date = State()
    time = State()
    review = State()
    confirm = State()


async def open_event_management(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Event management requires a private chat")
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role not in {"TEACHER", "STUDENT"}:
        raise ValueError("This account cannot manage lessons")
    await manager.start(
        LessonManagementScreens.menu,
        data={
            "view": view.model_dump(mode="json"),
            "viewer_timezone": view.account.timezone,
            "kind": None,
            "skip": 0,
            "cancel_skip": 0,
            "items": [],
            "total": 0,
            "detail": None,
            "error": "",
            "notice": "",
            "needs_reload": False,
            "outcome_unknown": False,
            "operation": None,
        },
        mode=StartMode.NORMAL,
        show_mode=ShowMode.EDIT,
    )


async def management_started(start_data: Any, manager: DialogManager) -> None:
    manager.dialog_data.clear()
    manager.dialog_data.update(start_data)


def _kind(manager: DialogManager) -> EventKind:
    value = manager.dialog_data.get("kind")
    if value == "once":
        return "once"
    if value == "weekly":
        return "weekly"
    raise ValueError("Lesson list type is missing")


def _resource(manager: DialogManager) -> ManagementResource:
    return "lessons" if _kind(manager) == "once" else "recurrent-lessons"


def _detail(manager: DialogManager) -> EventManagementDetail:
    return EventManagementDetail.model_validate(manager.dialog_data["detail"])


async def choose_one_time(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(callback, manager, "once", 0)


async def choose_weekly(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(callback, manager, "weekly", 0)


async def _load_page(
    callback: CallbackQuery, manager: DialogManager, kind: EventKind, skip: int
) -> None:
    if callback.message is None:
        raise ValueError("Event management requires a private chat")
    data = manager.dialog_data
    try:
        page = await backend(manager).read_management_page(
            callback.from_user.id,
            callback.message.chat.id,
            "lessons" if kind == "once" else "recurrent-lessons",
            skip=skip,
            limit=MANAGEMENT_PAGE_SIZE,
        )
    except (BackendError, BackendUnavailable) as exc:
        data.update(kind=kind, skip=skip, items=[], total=0, error=str(exc))
    else:
        data.update(
            kind=kind,
            skip=skip,
            items=[item.model_dump(mode="json") for item in page.items],
            total=page.total,
            error="",
        )
    await manager.switch_to(LessonManagementScreens.events, show_mode=ShowMode.EDIT)


async def refresh_events(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(
        callback, manager, _kind(manager), int(manager.dialog_data["skip"])
    )


async def previous_events(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(
        callback,
        manager,
        _kind(manager),
        max(0, int(manager.dialog_data["skip"]) - MANAGEMENT_PAGE_SIZE),
    )


async def next_events(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(
        callback,
        manager,
        _kind(manager),
        int(manager.dialog_data["skip"]) + MANAGEMENT_PAGE_SIZE,
    )


async def select_managed_lesson(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Event management requires a private chat")
    selected = next(
        (
            entry
            for entry in manager.dialog_data.get("items", [])
            if str(entry["id"]) == item
        ),
        None,
    )
    if selected is None:
        manager.dialog_data["error"] = "Reload the list and select a current lesson."
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
        cancel_skip=0,
        notice="",
        error="",
        needs_reload=False,
        outcome_unknown=False,
        operation=None,
    )
    await manager.switch_to(LessonManagementScreens.detail, show_mode=ShowMode.EDIT)


def _format_datetime(value: datetime, timezone: str) -> str:
    return format_human_datetime_in_timezone(
        value,
        timezone,
        reference_date=datetime.now(ZoneInfo(timezone)).date(),
    )


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


def _automatic_break_text(
    minutes: int,
    *,
    relation: str,
) -> str:
    if minutes == 0:
        return f"No automatic break is reserved after {relation}."
    return f"Autobreak after {relation}: {format_duration(minutes)}."


def _list_label(item: EventManagementItem, timezone: str) -> str:
    recurrence = "Weekly" if item.is_recurrent else "One time"
    label = (
        f"{recurrence} · {item.subject_label} · "
        f"{_format_datetime(item.start, timezone)}"
    )
    if item.is_recurrent and item.cancelled_occurrences:
        label += f" · {item.cancelled_occurrences} cancelled"
    if item.visibility_status == "hidden_by_vacation":
        label += " · hidden by vacation"
    return label


async def event_list_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    items: list[EventManagementItem] = [
        EventManagementItem.model_validate(item) for item in data.get("items", [])
    ]
    skip = int(data.get("skip", 0))
    total = int(data.get("total", 0))
    return {
        "title": "One-time lessons" if data.get("kind") == "once" else "Weekly lessons",
        "events": [
            (_list_label(item, data["viewer_timezone"]), str(item.id)) for item in items
        ],
        "empty_state": "No lessons." if total == 0 else "",
        "timezone": escape(data["viewer_timezone"]),
        "error": escape(str(data.get("error", ""))),
        "notice": escape(str(data.get("notice", ""))),
        "has_previous": skip > 0,
        "has_next": skip + len(items) < total,
        "is_once": data.get("kind") == "once",
        "is_weekly": data.get("kind") == "weekly",
    }


async def back_to_management_menu(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.switch_to(LessonManagementScreens.menu, show_mode=ShowMode.EDIT)


def _format_detail(detail: EventManagementDetail, timezone: str) -> str:
    text = (
        f"{detail.subject_label}\n"
        f"First lesson: {_format_interval(detail.start, detail.end, timezone)}"
    )
    if detail.is_recurrent:
        assert detail.interval is not None
        recurrence_timezone = detail.recurrence_timezone or "UTC"
        inclusive_end = (
            detail.interval_end.astimezone(ZoneInfo(timezone)).date()
            - timedelta(days=1)
            if detail.interval_end is not None
            else None
        )
        recurrence_timezone_note = (
            f"\nRecurrence timezone: {recurrence_timezone}"
            if recurrence_timezone != timezone
            else ""
        )
        text += (
            f"\nWeekly interval: every {detail.interval} day(s)"
            f"\nSeries end (inclusive): "
            f"{_format_date(inclusive_end, timezone) if inclusive_end else 'No end'}"
            f"\nCancelled occurrences: {detail.cancelled_occurrences}"
            f"{recurrence_timezone_note}"
        )
        if detail.event_type == "LESSON":
            text += "\n" + _automatic_break_text(
                detail.post_break_minutes,
                relation="each lesson",
            )
        if detail.cancels:
            text += (
                "\nSelect a cancelled occurrence below to inspect its exact interval."
            )
    else:
        if detail.event_type == "LESSON":
            text += "\n" + _automatic_break_text(
                detail.post_break_minutes,
                relation="this lesson",
            )
        if detail.visibility_status == "hidden_by_vacation":
            text += "\nThis lesson is hidden by a vacation but remains manageable."
    if detail.inspected_occurrence is not None:
        occurrence = detail.inspected_occurrence
        status = {
            "visible": "visible",
            "hidden_by_vacation": "hidden by vacation",
            "cancelled": "cancelled",
        }[occurrence.status]
        text += (
            f"\nSelected occurrence: "
            f"{_format_interval(occurrence.start, occurrence.end, timezone)} "
            f"({status})"
        )
        if detail.event_type == "LESSON":
            if occurrence.status == "cancelled":
                text += (
                    "\nNo automatic break is reserved after this cancelled occurrence."
                )
            else:
                text += "\n" + _automatic_break_text(
                    detail.post_break_minutes,
                    relation="this occurrence",
                )
    text += f"\nTimezone: {timezone}"
    return text


async def detail_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    detail = EventManagementDetail.model_validate(data["detail"])
    cancel_skip = int(data.get("cancel_skip", 0))
    cancellation_items = [
        (
            f"Cancelled · {_format_datetime(item.start, data['viewer_timezone'])}",
            item.start.isoformat(),
        )
        for item in detail.cancels[cancel_skip : cancel_skip + 10]
    ]
    blocked = bool(data.get("needs_reload") or data.get("outcome_unknown"))
    return {
        "detail_text": escape(_format_detail(detail, data["viewer_timezone"])),
        "cancellation_items": cancellation_items,
        "notice": escape(str(data.get("notice", ""))),
        "error": escape(str(data.get("error", ""))),
        "is_recurrent": detail.is_recurrent,
        "can_edit_cutoff": (
            detail.is_recurrent and detail.event_type == "LESSON" and not blocked
        ),
        "has_occurrence": detail.inspected_occurrence is not None,
        "can_manage_occurrence": (
            detail.inspected_occurrence is not None
            and detail.inspected_occurrence.status != "cancelled"
            and not blocked
        ),
        "can_manage_event": not blocked,
        "can_reload": blocked,
        "has_cancellations": bool(cancellation_items),
        "has_previous_cancellations": cancel_skip > 0,
        "has_next_cancellations": cancel_skip + len(cancellation_items)
        < len(detail.cancels),
    }


async def begin_inspect_occurrence(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _begin_date(manager, "inspect", "the recurring lesson occurrence")


async def inspect_cancelled_occurrence(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Event management requires a private chat")
    detail = _detail(manager)
    cancel_skip = int(manager.dialog_data.get("cancel_skip", 0))
    starts = {
        cancel.start.isoformat()
        for cancel in detail.cancels[cancel_skip : cancel_skip + 10]
    }
    if item not in starts:
        manager.dialog_data["error"] = (
            "Reload the series and select a current cancellation."
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    await _reload_detail(
        callback, manager, occurrence_start=datetime.fromisoformat(item)
    )


async def clear_inspected_occurrence(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    detail = _detail(manager)
    data["detail"] = detail.model_copy(
        update={"inspected_occurrence": None}
    ).model_dump(mode="json")
    data.update(notice="", error="")
    await manager.show(show_mode=ShowMode.EDIT)


async def previous_cancellations(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["cancel_skip"] = max(
        0, int(manager.dialog_data.get("cancel_skip", 0)) - 10
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def next_cancellations(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["cancel_skip"] = (
        int(manager.dialog_data.get("cancel_skip", 0)) + 10
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def begin_edit(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    detail = _detail(manager)
    if detail.inspected_occurrence is not None:
        manager.dialog_data["detail"] = detail.model_copy(
            update={"inspected_occurrence": None}
        ).model_dump(mode="json")
    await _begin_date(manager, "edit", "the new lesson start date")


async def _begin_date(
    manager: DialogManager, action: ManagementAction, prompt: str
) -> None:
    manager.dialog_data.update(
        date_action=action,
        date_prompt=f"Enter {prompt} as DD MM, DD:MM or DD/MM, optionally with year.",
        date_value=None,
        resolution=None,
        error="",
        notice="",
    )
    await manager.switch_to(LessonManagementScreens.date, show_mode=ShowMode.EDIT)


async def date_input(
    message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    if message.from_user is None:
        raise ValueError("Event management requires a private chat")
    try:
        timezone = ZoneInfo(manager.dialog_data["viewer_timezone"])
        parsed = parse_schedule_date(value, datetime.now(timezone).date())
    except (OverflowError, ValueError) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data["date_value"] = parsed.value.isoformat()
    manager.dialog_data["date_confirmation"] = (
        format_schedule_date_confirmation(parsed)
        if parsed.requires_confirmation
        else ""
    )
    if parsed.requires_confirmation:
        await delete_accepted_date_message(message, manager)
        await manager.switch_to(
            LessonManagementScreens.confirm_date, show_mode=ShowMode.EDIT
        )
        return
    if await _continue_date(message.from_user.id, message.chat.id, manager):
        if not await delete_accepted_date_message(message, manager):
            await manager.show(show_mode=ShowMode.EDIT)


async def confirm_date(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Event management requires a private chat")
    await _continue_date(callback.from_user.id, callback.message.chat.id, manager)


async def change_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(date_value=None, date_confirmation="", error="")
    await manager.switch_to(LessonManagementScreens.date, show_mode=ShowMode.EDIT)


async def _continue_date(sender_id: int, chat_id: int, manager: DialogManager) -> bool:
    data = manager.dialog_data
    action: ManagementAction = data["date_action"]
    if action == "cutoff":
        try:
            cutoff_day = date.fromisoformat(data["date_value"]) + timedelta(days=1)
            boundary = datetime.combine(cutoff_day, time.min)
            start = boundary - timedelta(minutes=1)
            resolution = await backend(manager).resolve_local_interval(
                sender_id, chat_id, start, boundary
            )
            cutoff = resolution.end.utc
            new_start = data["resolution"]["start"]["utc"]
            if cutoff <= datetime.fromisoformat(new_start):
                raise ValueError("The series end date must include the first lesson.")
        except (BackendError, BackendUnavailable, OverflowError, ValueError) as exc:
            data["error"] = str(exc)
            await manager.show(show_mode=ShowMode.EDIT)
            return False
        data.update(interval_end=cutoff.isoformat(), error="")
        await manager.switch_to(LessonManagementScreens.review, show_mode=ShowMode.EDIT)
        return True

    data["error"] = ""
    data["time_prompt"] = {
        "inspect": "Enter the occurrence start time shown in your schedule.",
        "edit": "Enter the new lesson start time in your timezone.",
        "move": "Enter the replacement start date's local time.",
    }[action]
    await manager.switch_to(LessonManagementScreens.time, show_mode=ShowMode.EDIT)
    return True


async def begin_edit_cutoff(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    detail = _detail(manager)
    cutoff_data = manager.dialog_data.get(
        "interval_end",
        detail.interval_end.isoformat() if detail.interval_end is not None else None,
    )
    cutoff = datetime.fromisoformat(cutoff_data) if cutoff_data is not None else None
    await _open_cutoff_editor(manager, detail, cutoff)


async def begin_edit_cutoff_from_detail(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    detail = _detail(manager)
    await _open_cutoff_editor(
        manager, detail, detail.interval_end, reset_resolution=True
    )


async def _open_cutoff_editor(
    manager: DialogManager,
    detail: EventManagementDetail,
    cutoff: datetime | None,
    *,
    reset_resolution: bool = False,
) -> None:
    if not detail.is_recurrent or detail.event_type != "LESSON":
        raise ValueError("Only a recurring lesson can have an editable cutoff.")
    if reset_resolution or "resolution" not in manager.dialog_data:
        manager.dialog_data["resolution"] = {"start": {"utc": detail.start.isoformat()}}
    manager.dialog_data["interval_end"] = cutoff.isoformat() if cutoff else None
    _prepare_cutoff_editor(manager, cutoff)
    await manager.switch_to(LessonManagementScreens.date, show_mode=ShowMode.EDIT)


def _prepare_cutoff_editor(manager: DialogManager, cutoff: datetime | None) -> None:
    timezone = ZoneInfo(manager.dialog_data["viewer_timezone"])
    cutoff_date = (
        cutoff.astimezone(timezone).date() - timedelta(days=1)
        if cutoff is not None
        else None
    )
    manager.dialog_data.update(
        date_action="cutoff",
        date_value=cutoff_date.isoformat() if cutoff_date else None,
        date_prompt=(
            "Enter the last weekly lesson date (inclusive), "
            "using DD MM, DD:MM or DD/MM."
        ),
        date_confirmation="",
        error="",
        notice="",
    )


async def choose_no_end(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("date_action") != "cutoff":
        raise ValueError("The optional series cutoff editor is not active.")
    manager.dialog_data.update(interval_end=None, error="", notice="")
    await manager.switch_to(LessonManagementScreens.review, show_mode=ShowMode.EDIT)


async def back_from_date(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("date_action") == "cutoff":
        manager.dialog_data.update(error="", notice="")
        await manager.switch_to(LessonManagementScreens.review, show_mode=ShowMode.EDIT)
        return
    await back_to_detail(_callback, _button, manager)


async def time_input(
    message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    if message.from_user is None:
        raise ValueError("Event management requires a private chat")
    try:
        clock = parse_schedule_time(value)
        local_start = combine_schedule_date_time(
            date.fromisoformat(manager.dialog_data["date_value"]), clock
        )
        resolution = await backend(manager).resolve_local_interval(
            message.from_user.id,
            message.chat.id,
            local_start,
            local_start + timedelta(minutes=1),
        )
    except (BackendError, BackendUnavailable, OverflowError, ValueError) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return

    action: ManagementAction = manager.dialog_data["date_action"]
    manager.dialog_data["error"] = ""
    if action == "inspect":
        try:
            detail = await backend(manager).read_management_detail(
                message.from_user.id,
                message.chat.id,
                _resource(manager),
                _detail(manager).id,
                occurrence_start=resolution.start.utc,
            )
        except (BackendError, BackendUnavailable) as exc:
            manager.dialog_data["error"] = str(exc)
            await manager.show(show_mode=ShowMode.EDIT)
            return
        manager.dialog_data.update(
            detail=detail.model_dump(mode="json"),
            cancel_skip=0,
            error="",
            notice="",
            needs_reload=False,
            outcome_unknown=False,
        )
        await delete_accepted_input_message(message, manager, "time")
        await manager.switch_to(LessonManagementScreens.detail, show_mode=ShowMode.EDIT)
        return
    if action == "edit":
        manager.dialog_data["resolution"] = resolution.model_dump(mode="json")
        detail = _detail(manager)
        if detail.is_recurrent:
            manager.dialog_data["interval_end"] = (
                detail.interval_end.isoformat()
                if detail.interval_end is not None
                else None
            )
            start, _ = _resolved_interval(
                resolution.model_dump(mode="json"), _duration_seconds(detail)
            )
            if detail.interval_end is not None and detail.interval_end <= start:
                await _open_cutoff_editor(manager, detail, detail.interval_end)
                if not await delete_accepted_input_message(message, manager, "time"):
                    await manager.show(show_mode=ShowMode.EDIT)
                return
            await delete_accepted_input_message(message, manager, "time")
            await manager.switch_to(
                LessonManagementScreens.review, show_mode=ShowMode.EDIT
            )
        else:
            await delete_accepted_input_message(message, manager, "time")
            await manager.switch_to(
                LessonManagementScreens.review, show_mode=ShowMode.EDIT
            )
        return
    if action == "move":
        manager.dialog_data["move_resolution"] = resolution.model_dump(mode="json")
        await delete_accepted_input_message(message, manager, "time")
        await manager.switch_to(LessonManagementScreens.review, show_mode=ShowMode.EDIT)
        return
    raise ValueError("Unsupported event management action")


async def begin_move_occurrence(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _begin_date(manager, "move", "the replacement lesson start date")


def _duration_seconds(detail: EventManagementDetail) -> int:
    duration = int((detail.end - detail.start).total_seconds())
    if duration <= 0:
        raise ValueError("Stored lesson duration must be positive.")
    return duration


def _format_duration_seconds(duration_seconds: int) -> str:
    minutes, seconds = divmod(duration_seconds, 60)
    formatted = format_duration(minutes)
    if seconds:
        formatted += f" {seconds} seconds"
    return formatted


def _resolved_interval(
    resolution_data: dict[str, Any], duration_seconds: int
) -> tuple[datetime, datetime]:
    start = datetime.fromisoformat(resolution_data["start"]["utc"])
    try:
        end = start + timedelta(seconds=duration_seconds)
    except OverflowError as exc:
        raise ValueError("Lesson duration is outside the supported range.") from exc
    if end <= start:
        raise ValueError("Lesson duration must be positive.")
    return start, end


def _review_text(manager: DialogManager) -> str:
    data = manager.dialog_data
    detail = _detail(manager)
    timezone = ZoneInfo(data["viewer_timezone"])
    if data["date_action"] == "move":
        occurrence = detail.inspected_occurrence
        if occurrence is None:
            raise ValueError("Inspect an occurrence before moving it.")
        start, end = _resolved_interval(
            data["move_resolution"], _duration_seconds(detail)
        )
        return (
            f"Move this exact occurrence?\n"
            f"From: {_format_interval(occurrence.start, occurrence.end, data['viewer_timezone'])}\n"
            f"To: {_format_interval(start, end, data['viewer_timezone'])}\n"
            f"Duration: {_format_duration_seconds(_duration_seconds(detail))}\n"
            "The original weekly occurrence will be cancelled and replaced by "
            "a one-time lesson.\n"
            + _automatic_break_text(
                detail.post_break_minutes,
                relation="replacement lesson",
            )
            + f"\nTimezone: {data['viewer_timezone']}"
        )

    resolution = data["resolution"]
    start, end = _resolved_interval(resolution, _duration_seconds(detail))
    text = (
        f"Review lesson change for {detail.subject_label}\n"
        f"Lesson time: {_format_interval(start, end, data['viewer_timezone'])}\n"
        f"Duration: {_format_duration_seconds(_duration_seconds(detail))}\n"
        f"Timezone: {data['viewer_timezone']}"
    )
    text += "\n" + _automatic_break_text(
        detail.post_break_minutes,
        relation="each lesson" if detail.is_recurrent else "this lesson",
    )
    if detail.is_recurrent:
        cutoff_data = data.get("interval_end")
        cutoff = datetime.fromisoformat(cutoff_data) if cutoff_data else None
        if cutoff is None:
            text += "\nLast weekly lesson (inclusive): No end"
        else:
            inclusive_end = _format_date(
                cutoff.astimezone(timezone).date() - timedelta(days=1),
                data["viewer_timezone"],
            )
            text += f"\nLast weekly lesson (inclusive): {inclusive_end}"
        recurrence_timezone = detail.recurrence_timezone or "UTC"
        if recurrence_timezone != data["viewer_timezone"]:
            text += f"\nWeekly starts use {recurrence_timezone}."
        old_start, old_end = detail.start, detail.end
        if start != old_start or end != old_end or cutoff != detail.interval_end:
            text += (
                "\nChanging recurrence dates or time clears saved individual "
                "cancellations under the existing series-update contract."
            )
    return text


async def review_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    detail_data = data.get("detail")
    detail = (
        EventManagementDetail.model_validate(detail_data)
        if detail_data is not None
        else None
    )
    try:
        review = _review_text(dialog_manager)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        review = f"Review could not be displayed: {exc}"
    return {
        "review": escape(review),
        "error": escape(str(data.get("error", ""))),
        "notice": escape(str(data.get("notice", ""))),
        "save_label": (
            "Move occurrence" if data.get("date_action") == "move" else "Save change"
        ),
        "is_move": data.get("date_action") == "move",
        "can_change_cutoff": (
            detail is not None
            and detail.is_recurrent
            and detail.event_type == "LESSON"
            and data.get("date_action") != "move"
            and not data.get("needs_reload")
            and not data.get("outcome_unknown")
        ),
        "is_cutoff_action": data.get("date_action") == "cutoff",
    }


async def begin_delete(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    detail = _detail(manager)
    manager.dialog_data.update(
        confirm_action="delete",
        confirm_text=(
            (
                "Delete the entire weekly series and all remaining occurrences?"
                if detail.is_recurrent
                else "Delete this one-time lesson?"
            )
            + (
                " Its automatic break reservations will also be removed."
                if detail.post_break_minutes > 0
                else ""
            )
        ),
        error="",
        notice="",
    )
    await manager.switch_to(LessonManagementScreens.confirm, show_mode=ShowMode.EDIT)


async def begin_cancel_occurrence(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    detail = _detail(manager)
    occurrence = detail.inspected_occurrence
    if occurrence is None or occurrence.status == "cancelled":
        manager.dialog_data["error"] = (
            "Reload the series before changing this occurrence."
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(
        confirm_action="cancel_occurrence",
        confirm_text=(
            "Cancel only this weekly occurrence?\n"
            f"{_format_datetime(occurrence.start, manager.dialog_data['viewer_timezone'])}\n"
            f"Timezone: {manager.dialog_data['viewer_timezone']}\n"
            "The rest of the series will remain unchanged."
            + (
                "\nIts automatic break reservation will also be removed."
                if detail.post_break_minutes > 0
                else "\nNo automatic break is reserved after this occurrence."
            )
        ),
        error="",
        notice="",
    )
    await manager.switch_to(LessonManagementScreens.confirm, show_mode=ShowMode.EDIT)


async def confirm_action(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Event management requires a private chat")
    data = manager.dialog_data
    detail = _detail(manager)
    action = data.get("confirm_action")
    if data.get("submitting"):
        return
    data.update(submitting=True, error="")
    try:
        if action == "delete":
            await backend(manager).delete_management_event(
                _resource(manager),
                detail.id,
                callback.from_user.id,
                callback.message.chat.id,
                revision=detail.revision,
            )
            notice = "Lesson deleted."
            if detail.post_break_minutes > 0:
                notice += " Its automatic break reservations were also removed."
            data.update(submitting=False, operation="delete", notice=notice)
            await _load_page(callback, manager, _kind(manager), int(data["skip"]))
            manager.dialog_data["notice"] = notice
            return
        if action == "cancel_occurrence":
            occurrence = detail.inspected_occurrence
            if occurrence is None:
                raise ValueError("Inspect an occurrence before cancelling it.")
            await backend(manager).cancel_recurrent_occurrence(
                callback.from_user.id,
                callback.message.chat.id,
                detail.id,
                RecurrentOccurrenceCancelPayload(
                    occurrence_start=occurrence.start,
                    occurrence_end=occurrence.end,
                ),
            )
            await _refresh_after_occurrence_action(
                callback,
                manager,
                occurrence.start,
                (
                    "This occurrence was cancelled. "
                    + (
                        "Its automatic break reservation was also removed."
                        if detail.post_break_minutes > 0
                        else "No automatic break was reserved after it."
                    )
                ),
            )
            return
        raise ValueError("Unknown confirmation action")
    except BackendOutcomeUnknown as exc:
        data.update(
            submitting=False,
            outcome_unknown=True,
            needs_reload=True,
            operation=action,
            error=str(exc),
            notice="The write outcome is unknown. Reload before taking another action.",
        )
    except BackendError as exc:
        data.update(
            submitting=False,
            needs_reload=exc.status == 409,
            operation=action if exc.status == 409 else None,
            error=str(exc),
            notice="",
        )
    except BackendUnavailable as exc:
        data.update(submitting=False, error=str(exc), notice="")
    except (ValidationError, ValueError, OverflowError) as exc:
        data.update(submitting=False, error=str(exc), notice="")
    await manager.show(show_mode=ShowMode.EDIT)


async def submit_review(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Event management requires a private chat")
    data = manager.dialog_data
    if (
        data.get("submitting")
        or data.get("needs_reload")
        or data.get("outcome_unknown")
    ):
        return
    detail = _detail(manager)
    action = data.get("date_action")
    data.update(submitting=True, error="", notice="")
    try:
        client = backend(manager)
        if action == "move":
            occurrence = detail.inspected_occurrence
            if occurrence is None or occurrence.status == "cancelled":
                raise ValueError("Reload the series and inspect an active occurrence.")
            start, end = _resolved_interval(
                data["move_resolution"], _duration_seconds(detail)
            )
            await client.move_recurrent_occurrence(
                callback.from_user.id,
                callback.message.chat.id,
                detail.id,
                RecurrentOccurrenceMovePayload(
                    occurrence_start=occurrence.start,
                    occurrence_end=occurrence.end,
                    replacement_start=start,
                    replacement_end=end,
                ),
            )
            await _refresh_after_occurrence_action(
                callback,
                manager,
                occurrence.start,
                (
                    "The occurrence was moved; its replacement is a one-time lesson. "
                    + _automatic_break_text(
                        detail.post_break_minutes,
                        relation="replacement lesson",
                    )
                ),
            )
            return

        start, end = _resolved_interval(data["resolution"], _duration_seconds(detail))
        payload: EventUpdatePayload
        if detail.is_recurrent:
            assert detail.interval is not None
            payload = RecurrentEventUpdatePayload(
                start=start,
                end=end,
                user_id=detail.user_id,
                interval=detail.interval,
                interval_end=(
                    datetime.fromisoformat(data["interval_end"])
                    if data.get("interval_end") is not None
                    else None
                ),
                event_type="LESSON",
            )
            await client.write_management_event(
                "PUT",
                "recurrent-lessons",
                callback.from_user.id,
                callback.message.chat.id,
                payload,
                RecurrentEventWriteResult,
                event_id=detail.id,
                revision=detail.revision,
            )
        else:
            payload = EventUpdatePayload(
                start=start,
                end=end,
                user_id=detail.user_id,
                event_type="LESSON",
            )
            await client.write_management_event(
                "PUT",
                "lessons",
                callback.from_user.id,
                callback.message.chat.id,
                payload,
                EventWriteResult,
                event_id=detail.id,
                revision=detail.revision,
            )
        data.update(submitting=False, operation="update", notice="Lesson updated.")
        await _refresh_detail_after_write(callback, manager)
        return
    except BackendOutcomeUnknown as exc:
        data.update(
            submitting=False,
            outcome_unknown=True,
            needs_reload=True,
            operation=action,
            error=str(exc),
            notice="The write outcome is unknown. Reload before taking another action.",
        )
    except BackendError as exc:
        stale = exc.status == 409
        data.update(
            submitting=False,
            needs_reload=stale,
            operation="stale" if stale else None,
            error=str(exc),
            notice="",
        )
    except BackendUnavailable as exc:
        data.update(submitting=False, error=str(exc), notice="")
    except (ValidationError, ValueError, OverflowError) as exc:
        data.update(submitting=False, error=str(exc), notice="")
    await manager.show(show_mode=ShowMode.EDIT)


async def _refresh_detail_after_write(
    callback: CallbackQuery, manager: DialogManager
) -> None:
    data = manager.dialog_data
    if callback.message is None:
        raise ValueError("Event management requires a private chat")
    try:
        detail = await backend(manager).read_management_detail(
            callback.from_user.id,
            callback.message.chat.id,
            _resource(manager),
            _detail(manager).id,
        )
    except (BackendError, BackendUnavailable) as exc:
        data.update(
            submitting=False,
            needs_reload=True,
            error=str(exc),
            notice="The write succeeded, but fresh details could not be loaded.",
        )
    else:
        data.update(
            detail=detail.model_dump(mode="json"),
            cancel_skip=0,
            submitting=False,
            needs_reload=False,
            outcome_unknown=False,
            operation=None,
            error="",
        )
    await manager.switch_to(LessonManagementScreens.detail, show_mode=ShowMode.EDIT)


async def _refresh_after_occurrence_action(
    callback: CallbackQuery,
    manager: DialogManager,
    occurrence_start: datetime,
    notice: str,
) -> None:
    data = manager.dialog_data
    if callback.message is None:
        raise ValueError("Event management requires a private chat")
    data.update(submitting=False, operation=None)
    try:
        refreshed = await backend(manager).read_management_detail(
            callback.from_user.id,
            callback.message.chat.id,
            _resource(manager),
            _detail(manager).id,
            occurrence_start=occurrence_start,
        )
    except (BackendError, BackendUnavailable) as exc:
        data.update(
            needs_reload=True,
            error=str(exc),
            notice=f"{notice} Fresh series details could not be loaded.",
        )
    else:
        data.update(
            detail=refreshed.model_dump(mode="json"),
            cancel_skip=0,
            needs_reload=False,
            outcome_unknown=False,
            error="",
            notice=notice,
        )
    await manager.switch_to(LessonManagementScreens.detail, show_mode=ShowMode.EDIT)


async def reload_management_state(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Event management requires a private chat")
    data = manager.dialog_data
    if data.get("operation") == "delete":
        try:
            await backend(manager).read_management_detail(
                callback.from_user.id,
                callback.message.chat.id,
                _resource(manager),
                _detail(manager).id,
            )
        except BackendError as exc:
            if exc.status == 404:
                await _load_page(callback, manager, _kind(manager), int(data["skip"]))
                manager.dialog_data.update(
                    outcome_unknown=False,
                    needs_reload=False,
                    notice="The lesson is no longer available.",
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
            needs_reload=False,
            outcome_unknown=False,
            notice="The lesson still exists. Its current details were reloaded.",
            operation=None,
        )
        await _reload_detail(callback, manager)
        return

    detail = _detail(manager)
    occurrence_start = (
        detail.inspected_occurrence.start
        if detail.inspected_occurrence is not None
        else None
    )
    previous_outcome_unknown = bool(data.get("outcome_unknown"))
    await _load_detail(
        manager,
        callback.from_user.id,
        callback.message.chat.id,
        occurrence_start=occurrence_start,
    )
    if previous_outcome_unknown:
        manager.dialog_data["notice"] = (
            "The previous write outcome was unknown. Current details were reloaded; "
            "no write was repeated."
        )


async def _reload_detail(
    callback: CallbackQuery,
    manager: DialogManager,
    *,
    occurrence_start: datetime | None = None,
) -> None:
    if callback.message is None:
        raise ValueError("Event management requires a private chat")
    await _load_detail(
        manager,
        callback.from_user.id,
        callback.message.chat.id,
        occurrence_start=occurrence_start,
    )


async def _load_detail(
    manager: DialogManager,
    sender_id: int,
    chat_id: int,
    *,
    occurrence_start: datetime | None = None,
) -> None:
    data = manager.dialog_data
    try:
        detail = await backend(manager).read_management_detail(
            sender_id,
            chat_id,
            _resource(manager),
            _detail(manager).id,
            occurrence_start=occurrence_start,
        )
    except BackendError as exc:
        data.update(error=str(exc), needs_reload=True)
    except BackendUnavailable as exc:
        data.update(error=str(exc), needs_reload=True)
    else:
        data.update(
            detail=detail.model_dump(mode="json"),
            cancel_skip=0,
            error="",
            needs_reload=False,
            outcome_unknown=False,
        )
    await manager.switch_to(LessonManagementScreens.detail, show_mode=ShowMode.EDIT)


async def back_to_list(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_page(
        callback, manager, _kind(manager), int(manager.dialog_data["skip"])
    )


async def return_to_home(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.done(show_mode=ShowMode.EDIT)


def build_event_management_dialog() -> Dialog:
    return Dialog(
        Window(
            Const("Manage lessons"),
            Button(
                Const("One-time lessons"),
                id="once",
                on_click=choose_one_time,
            ),
            Button(Const("Weekly lessons"), id="weekly", on_click=choose_weekly),
            Button(Const("Back to home"), id="home", on_click=return_to_home),
            state=LessonManagementScreens.menu,
        ),
        Window(
            Format("{title}\n{empty_state}\n{notice}\n{error}\nTimezone: {timezone}"),
            Select(
                Format("{item[0]}"),
                id="managed_lesson",
                item_id_getter=lambda item: item[1],
                items="events",
                on_click=select_managed_lesson,
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
                Button(Const("Refresh"), id="refresh", on_click=refresh_events),
                Button(
                    Const("Weekly lessons"),
                    id="weekly",
                    on_click=choose_weekly,
                    when="is_once",
                ),
                Button(
                    Const("One-time lessons"),
                    id="once",
                    on_click=choose_one_time,
                    when="is_weekly",
                ),
            ),
            Button(Const("Back"), id="back", on_click=back_to_management_menu),
            state=LessonManagementScreens.events,
        ),
        Window(
            Format("{detail_text}\n{notice}\n{error}"),
            Button(
                Const("Edit lesson or series"),
                id="edit",
                on_click=begin_edit,
                when="can_manage_event",
            ),
            Button(
                Const("Change series end"),
                id="change_cutoff_from_detail",
                on_click=begin_edit_cutoff_from_detail,
                when="can_edit_cutoff",
            ),
            Button(
                Const("Delete lesson or series"),
                id="delete",
                on_click=begin_delete,
                when="can_manage_event",
            ),
            Button(
                Const("Inspect occurrence"),
                id="inspect",
                on_click=begin_inspect_occurrence,
                when="is_recurrent",
            ),
            Select(
                Format("{item[0]}"),
                id="cancelled_occurrence",
                item_id_getter=lambda item: item[1],
                items="cancellation_items",
                on_click=inspect_cancelled_occurrence,
                when="has_cancellations",
            ),
            Row(
                Button(
                    Const("◀ Previous cancellations"),
                    id="previous_cancellations",
                    on_click=previous_cancellations,
                    when="has_previous_cancellations",
                ),
                Button(
                    Const("More cancellations ▶"),
                    id="next_cancellations",
                    on_click=next_cancellations,
                    when="has_next_cancellations",
                ),
            ),
            Button(
                Const("Cancel this occurrence"),
                id="cancel_occurrence",
                on_click=begin_cancel_occurrence,
                when="can_manage_occurrence",
            ),
            Button(
                Const("Move this occurrence"),
                id="move_occurrence",
                on_click=begin_move_occurrence,
                when="can_manage_occurrence",
            ),
            Button(
                Const("Clear occurrence selection"),
                id="clear_occurrence",
                on_click=clear_inspected_occurrence,
                when="has_occurrence",
            ),
            Button(
                Const("Reload event"),
                id="reload",
                on_click=reload_management_state,
                when="can_reload",
            ),
            Button(Const("Back to list"), id="list", on_click=back_to_list),
            state=LessonManagementScreens.detail,
        ),
        Window(
            Format("{date_prompt}\n{error}"),
            TextInput("date_input", on_success=date_input),
            Button(
                Const("No end"),
                id="no_end",
                on_click=choose_no_end,
                when="is_cutoff_action",
            ),
            Button(Const("Back"), id="back", on_click=back_from_date),
            state=LessonManagementScreens.date,
        ),
        Window(
            Format("{date_confirmation}\nConfirm this inferred next-year date."),
            Button(Const("Confirm date"), id="confirm", on_click=confirm_date),
            Button(Const("Change date"), id="change", on_click=change_date),
            Button(Const("Back"), id="back", on_click=back_from_date),
            state=LessonManagementScreens.confirm_date,
        ),
        Window(
            Format("{time_prompt}\nTimezone: {viewer_timezone}\n{error}"),
            TextInput("time_input", on_success=time_input),
            Button(Const("Change date"), id="date", on_click=change_date),
            Button(Const("Back to event"), id="detail", on_click=back_to_detail),
            state=LessonManagementScreens.time,
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
                Const("Change date/time"),
                id="edit",
                on_click=edit_again,
                when="can_submit",
            ),
            Button(
                Const("Change series end"),
                id="change_cutoff",
                on_click=begin_edit_cutoff,
                when="can_change_cutoff",
            ),
            Button(
                Const("Reload event"),
                id="reload",
                on_click=reload_management_state,
                when="can_reload",
            ),
            Button(Const("Back to event"), id="back", on_click=back_to_detail),
            state=LessonManagementScreens.review,
        ),
        Window(
            Format("{confirm_text}\n{error}"),
            Button(
                Const("Confirm"),
                id="confirm",
                on_click=confirm_action,
                when="can_confirm",
            ),
            Button(
                Const("Reload event"),
                id="reload",
                on_click=reload_management_state,
                when="can_reload",
            ),
            Button(Const("Cancel"), id="cancel", on_click=back_to_detail),
            state=LessonManagementScreens.confirm,
        ),
        getter=management_data,
        on_start=management_started,
    )


async def management_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    detail = data.get("detail")
    detail_model = (
        EventManagementDetail.model_validate(detail) if detail is not None else None
    )
    result = await event_list_data(dialog_manager)
    result.update(
        {
            "date_prompt": escape(str(data.get("date_prompt", ""))),
            "date_confirmation": escape(str(data.get("date_confirmation", ""))),
            "time_prompt": escape(str(data.get("time_prompt", ""))),
            "confirm_text": escape(str(data.get("confirm_text", ""))),
            "viewer_timezone": data["viewer_timezone"],
        }
    )
    if detail_model is not None:
        result.update(await detail_data(dialog_manager))
    result.update(await review_data(dialog_manager))
    blocked = bool(data.get("needs_reload") or data.get("outcome_unknown"))
    result.update(
        can_submit=not blocked and not data.get("submitting"),
        can_reload=blocked,
        can_confirm=not blocked and not data.get("submitting"),
    )
    return result


async def back_to_detail(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(error="", notice="")
    await manager.switch_to(LessonManagementScreens.detail, show_mode=ShowMode.EDIT)


async def edit_again(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _begin_date(manager, "edit", "the new lesson start date")

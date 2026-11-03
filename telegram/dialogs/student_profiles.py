from __future__ import annotations

import uuid
from datetime import UTC, datetime
from html import escape
from typing import Any

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, DialogManager, ShowMode, StartMode, Window
from aiogram_dialog.widgets.kbd import Button, Row, Select
from aiogram_dialog.widgets.text import Const, Format

from telegram.api import (
    BackendError,
    BackendUnavailable,
    SessionView,
    StudentRemovalConfirmRequest,
    StudentRemovalOutcomeUnknown,
    StudentRemovalPreview,
    StudentRemovalResult,
    TeacherStudentProfileDetail,
    TeacherStudentProfilePage,
    TeacherStudentProfileSummary,
)
from telegram.dialogs.homework.teacher_management import (
    open_teacher_homework_for_student,
)
from telegram.shared.access import backend
from telegram.shared.feedback import feedback_block

PROFILE_PAGE_SIZE = 20


class StudentProfileScreens(StatesGroup):
    active = State()
    former = State()
    detail = State()
    removal = State()
    result = State()
    unavailable = State()


def _list_state(kind: str) -> State:
    return (
        StudentProfileScreens.former
        if kind == "former"
        else StudentProfileScreens.active
    )


def _safe(value: object) -> str:
    return escape(" ".join(str(value).split()), quote=True)


def _format_utc(value: datetime | None) -> str:
    if value is None:
        return "Unknown"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _action_label(action_type: str) -> str:
    return action_type.replace("_", " ").lower().capitalize()


def _profile_detail_text(profile: TeacherStudentProfileDetail) -> str:
    homework = profile.homework
    lines = [
        f"<b>{_safe(profile.display_name)}</b>",
        f"Username: {_safe(profile.username_display)}",
        f"Added: {_format_utc(profile.linked_at)}",
        (
            f"Homework: {homework.completed}/{homework.assigned} complete, "
            f"{homework.incomplete} incomplete "
            f"({homework.completed_by_student} by student, "
            f"{homework.completed_by_teacher} by teacher)"
        ),
        "",
        "<b>Last 10 actions</b>",
    ]
    if not profile.actions:
        lines.append("No recorded actions.")
    for action in profile.actions:
        description = _action_label(action.action_type)
        if action.resource_available is False:
            reference = "Item no longer available"
        elif action.resource_type:
            reference = _safe(action.resource_type.replace("_", " "))
        else:
            reference = "Account"
        if action.resource_occurrence_at is not None:
            reference += f" · {_format_utc(action.resource_occurrence_at)}"
        lines.append(
            f"• {_format_utc(action.occurred_at)} — {_safe(description)} ({reference})"
        )
    if profile.removed_at is not None:
        lines.extend(
            [
                "",
                f"Removed from this teacher: {_format_utc(profile.removed_at)}",
                "This former-student profile is read-only.",
            ]
        )
    return "\n".join(lines)


def _impact_text(preview: StudentRemovalPreview) -> str:
    impact = preview.impact
    return (
        "<b>Review student removal</b>\n"
        f"Future one-off lessons to delete (including ongoing): "
        f"{impact.one_off_lessons_to_delete}\n"
        f"Recurring series to truncate: {impact.recurring_series_to_truncate}\n"
        f"Recurring series with no past occurrence to delete: "
        f"{impact.recurring_series_to_delete}\n"
        f"Future cancellations to discard: "
        f"{impact.future_cancellations_to_delete}\n"
        f"Staged uploads to revoke and clean up: {impact.staged_uploads_to_revoke}\n"
        f"Unsent notifications to suppress: {impact.notifications_to_suppress}\n\n"
        "The account stays active. Past lessons, homework, submissions, completion "
        "status, committed files, and action history are retained. Sent notification "
        "history is retained. The student may rejoin a teacher later."
    )


def _result_text(result: StudentRemovalResult) -> str:
    impact = result.impact
    return (
        "<b>Student removed from this teacher.</b>\n"
        f"Removed at: {_format_utc(result.removed_at)}\n"
        f"Past lessons retained; future one-off lessons removed: "
        f"{impact.one_off_lessons_to_delete}\n"
        "The account and homework history remain available to their authorized "
        "owners. The student can rejoin later."
    )


def _store_profile_page(
    data: dict[str, Any], page: TeacherStudentProfilePage, kind: str, skip: int
) -> None:
    data.update(
        profiles=[item.model_dump(mode="json") for item in page.items],
        profile_total=page.total,
        profile_skip=skip,
        list_kind=kind,
        error="",
    )


async def _load_profile_page(
    sender_id: int,
    chat_id: int,
    manager: DialogManager,
    kind: str,
    skip: int,
) -> bool:
    try:
        if kind == "former":
            page = await backend(manager).read_former_student_profiles(
                sender_id, chat_id, skip=skip, limit=PROFILE_PAGE_SIZE
            )
        else:
            page = await backend(manager).read_teacher_student_profiles(
                sender_id, chat_id, skip=skip, limit=PROFILE_PAGE_SIZE
            )
    except (BackendError, BackendUnavailable) as exc:
        manager.dialog_data["error"] = str(exc)
        return False
    _store_profile_page(manager.dialog_data, page, kind, skip)
    return True


async def open_teacher_student_profiles(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Student profiles require a private chat")
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role != "TEACHER":
        raise ValueError("Only teachers can view student profiles")
    data: dict[str, Any] = {
        "view": view.model_dump(mode="json"),
        "list_kind": "active",
        "profile_skip": 0,
        "profile_total": 0,
        "profiles": [],
        "profile": None,
        "removal_preview": None,
        "removal_request": None,
        "removal_result": None,
        "submitting": False,
        "outcome_unknown": False,
        "error": "",
        "warning": "",
    }
    try:
        page = await backend(manager).read_teacher_student_profiles(
            callback.from_user.id,
            callback.message.chat.id,
            skip=0,
            limit=PROFILE_PAGE_SIZE,
        )
    except (BackendError, BackendUnavailable) as exc:
        data["error"] = str(exc)
        loaded = False
    else:
        _store_profile_page(data, page, "active", 0)
        loaded = True
    state = (
        StudentProfileScreens.active if loaded else StudentProfileScreens.unavailable
    )
    await manager.start(
        state,
        data=data,
        mode=StartMode.NORMAL,
        show_mode=ShowMode.EDIT,
    )


async def student_profiles_started(start_data: Any, manager: DialogManager) -> None:
    manager.dialog_data.clear()
    manager.dialog_data.update(start_data)


async def previous_profiles(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _change_profile_page(callback, manager, -PROFILE_PAGE_SIZE)


async def next_profiles(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _change_profile_page(callback, manager, PROFILE_PAGE_SIZE)


async def _change_profile_page(
    callback: CallbackQuery, manager: DialogManager, delta: int
) -> None:
    if callback.message is None:
        raise ValueError("Student profiles require a private chat")
    data = manager.dialog_data
    total = int(data.get("profile_total", 0))
    current = int(data.get("profile_skip", 0))
    last_page = max(0, ((total - 1) // PROFILE_PAGE_SIZE) * PROFILE_PAGE_SIZE)
    skip = min(last_page, max(0, current + delta))
    await _load_profile_page(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        str(data.get("list_kind", "active")),
        skip,
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def show_former_students(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _switch_profile_list(callback, manager, "former")


async def show_active_students(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _switch_profile_list(callback, manager, "active")


async def _switch_profile_list(
    callback: CallbackQuery,
    manager: DialogManager,
    kind: str,
    *,
    warning: str = "",
) -> None:
    if callback.message is None:
        raise ValueError("Student profiles require a private chat")
    data = manager.dialog_data
    data.update(profile=None, warning=warning, error="", list_kind=kind)
    loaded = await _load_profile_page(
        callback.from_user.id, callback.message.chat.id, manager, kind, 0
    )
    if loaded:
        await manager.switch_to(_list_state(kind), show_mode=ShowMode.EDIT)
    else:
        await manager.switch_to(
            StudentProfileScreens.unavailable, show_mode=ShowMode.EDIT
        )


async def refresh_profiles(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Student profiles require a private chat")
    data = manager.dialog_data
    await _load_profile_page(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        str(data.get("list_kind", "active")),
        int(data.get("profile_skip", 0)),
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def select_profile(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Student profiles require a private chat")
    selected = next(
        (
            entry
            for entry in manager.dialog_data.get("profiles", [])
            if str(entry["relationship_id"]) == item
        ),
        None,
    )
    if selected is None:
        manager.dialog_data["error"] = "Select a student from the current page."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    kind = str(manager.dialog_data.get("list_kind", "active"))
    try:
        if kind == "former":
            profile = await backend(manager).read_former_student_profile(
                callback.from_user.id,
                callback.message.chat.id,
                uuid.UUID(item),
            )
        else:
            profile = await backend(manager).read_teacher_student_profile(
                callback.from_user.id,
                callback.message.chat.id,
                uuid.UUID(str(selected["student_id"])),
            )
    except BackendError as exc:
        if exc.status == 404:
            await _switch_profile_list(
                callback,
                manager,
                kind,
                warning="This student changed status. The list was refreshed.",
            )
            return
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(
        profile=profile.model_dump(mode="json"),
        list_kind=kind,
        error="",
        warning="",
        removal_preview=None,
        removal_request=None,
        outcome_unknown=False,
    )
    await manager.switch_to(StudentProfileScreens.detail, show_mode=ShowMode.EDIT)


async def refresh_profile(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Student profile requires a private chat")
    data = manager.dialog_data
    raw_profile = data.get("profile")
    if not isinstance(raw_profile, dict):
        manager.dialog_data["error"] = "Select a student from the current page."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    try:
        profile = TeacherStudentProfileDetail.model_validate(raw_profile)
        if data.get("list_kind") == "former":
            refreshed = await backend(manager).read_former_student_profile(
                callback.from_user.id,
                callback.message.chat.id,
                profile.relationship_id,
            )
        else:
            refreshed = await backend(manager).read_teacher_student_profile(
                callback.from_user.id,
                callback.message.chat.id,
                profile.student_id,
            )
    except BackendError as exc:
        if exc.status == 404:
            await _refresh_after_removal(callback, manager)
            return
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data.update(profile=refreshed.model_dump(mode="json"), error="", warning="")
    await manager.show(show_mode=ShowMode.EDIT)


async def _refresh_after_removal(
    callback: CallbackQuery,
    manager: DialogManager,
    *,
    warning: str = "This student is no longer active. The roster was refreshed.",
) -> None:
    if callback.message is None:
        raise ValueError("Student profile requires a private chat")
    data = manager.dialog_data
    data.update(
        profile=None,
        removal_preview=None,
        removal_request=None,
        outcome_unknown=False,
        warning=warning,
    )
    loaded = await _load_profile_page(
        callback.from_user.id, callback.message.chat.id, manager, "active", 0
    )
    await manager.switch_to(
        StudentProfileScreens.active if loaded else StudentProfileScreens.unavailable,
        show_mode=ShowMode.EDIT,
    )


async def begin_student_removal(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Student removal requires a private chat")
    data = manager.dialog_data
    raw_profile = data.get("profile")
    if data.get("list_kind") != "active" or not isinstance(raw_profile, dict):
        data["error"] = "Only an active student can be removed."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    profile = TeacherStudentProfileDetail.model_validate(raw_profile)
    try:
        preview = await backend(manager).preview_teacher_student_removal(
            callback.from_user.id, callback.message.chat.id, profile.student_id
        )
    except BackendError as exc:
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data.update(
        removal_preview=preview.model_dump(mode="json"),
        removal_request=None,
        submitting=False,
        outcome_unknown=False,
        error="",
        warning="",
    )
    await manager.switch_to(StudentProfileScreens.removal, show_mode=ShowMode.EDIT)


async def cancel_student_removal(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        return
    manager.dialog_data.update(
        removal_preview=None,
        removal_request=None,
        submitting=False,
        error="",
        warning="",
    )
    await manager.switch_to(StudentProfileScreens.detail, show_mode=ShowMode.EDIT)


async def confirm_student_removal(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        await _send_removal_confirmation(callback, manager, retry=True)
    else:
        await _send_removal_confirmation(callback, manager, retry=False)


async def retry_student_removal(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _send_removal_confirmation(callback, manager, retry=True)


async def _send_removal_confirmation(
    callback: CallbackQuery,
    manager: DialogManager,
    *,
    retry: bool,
) -> None:
    if callback.message is None:
        raise ValueError("Student removal requires a private chat")
    data = manager.dialog_data
    if data.get("submitting"):
        return
    raw_profile = data.get("profile")
    raw_preview = data.get("removal_preview")
    if not isinstance(raw_profile, dict) or not isinstance(raw_preview, dict):
        data.update(error="Request a fresh removal preview before confirming.")
        await manager.switch_to(StudentProfileScreens.detail, show_mode=ShowMode.EDIT)
        return
    profile = TeacherStudentProfileDetail.model_validate(raw_profile)
    if retry:
        raw_request = data.get("removal_request")
        if not isinstance(raw_request, dict):
            data.update(
                outcome_unknown=False,
                error="The confirmation details are unavailable. Request a fresh preview.",
                removal_preview=None,
            )
            await manager.switch_to(
                StudentProfileScreens.detail, show_mode=ShowMode.EDIT
            )
            return
        request = StudentRemovalConfirmRequest.model_validate(raw_request)
    else:
        preview = StudentRemovalPreview.model_validate(raw_preview)
        request = StudentRemovalConfirmRequest(
            preview_token=preview.confirmation_token,
            operation_key=uuid.uuid4(),
        )
        data["removal_request"] = request.model_dump(mode="json")

    data.update(submitting=True, error="", warning="")
    await manager.show(show_mode=ShowMode.EDIT)
    try:
        result = await backend(manager).confirm_teacher_student_removal(
            callback.from_user.id,
            callback.message.chat.id,
            profile.student_id,
            request,
        )
    except StudentRemovalOutcomeUnknown as exc:
        data.update(
            removal_request=exc.request.model_dump(mode="json"),
            submitting=False,
            outcome_unknown=True,
            error="",
            warning=str(exc),
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendError as exc:
        data.update(submitting=False, outcome_unknown=False)
        if exc.status in {404, 409}:
            data.update(
                removal_preview=None,
                removal_request=None,
                error="",
            )
            await _refresh_after_removal(
                callback,
                manager,
                warning=(
                    "The student or impact changed. Review the refreshed profile "
                    "and request a new preview."
                ),
            )
            return
        data.update(error=str(exc), removal_request=None)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        data.update(
            submitting=False,
            outcome_unknown=True,
            error="",
            warning=f"{exc} Retry only the same confirmation.",
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return

    if not result.cleanup_complete:
        data.update(
            submitting=False,
            outcome_unknown=False,
            error="Removal committed, but staged-file cleanup is incomplete.",
            removal_result=result.model_dump(mode="json"),
            removal_preview=None,
            removal_request=None,
        )
        await manager.switch_to(StudentProfileScreens.result, show_mode=ShowMode.EDIT)
        return
    data.update(
        removal_result=result.model_dump(mode="json"),
        removal_preview=None,
        removal_request=None,
        submitting=False,
        outcome_unknown=False,
        error="",
        warning="",
    )
    await manager.switch_to(StudentProfileScreens.result, show_mode=ShowMode.EDIT)


async def show_former_students_after_removal(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _switch_profile_list(
        callback, manager, "former", warning="Student removal was confirmed."
    )


async def retry_profile_list(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Student profile list requires a private chat")
    kind = str(manager.dialog_data.get("list_kind", "active"))
    loaded = await _load_profile_page(
        callback.from_user.id, callback.message.chat.id, manager, kind, 0
    )
    await manager.switch_to(
        _list_state(kind) if loaded else StudentProfileScreens.unavailable,
        show_mode=ShowMode.EDIT,
    )


async def close_student_profiles(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.done(show_mode=ShowMode.EDIT)


async def back_to_profile_list(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _switch_profile_list(
        callback, manager, str(manager.dialog_data.get("list_kind", "active"))
    )


async def student_profile_data(
    dialog_manager: DialogManager, **_: Any
) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    kind = str(data.get("list_kind", "active"))
    total = int(data.get("profile_total", 0))
    skip = int(data.get("profile_skip", 0))
    profiles = []
    for entry in data.get("profiles", []):
        item = TeacherStudentProfileSummary.model_validate(entry)
        homework = item.homework
        profiles.append(
            {
                "id": str(item.relationship_id),
                "label": (
                    f"{_safe(item.display_name)} · {_safe(item.username_display)} · "
                    f"{homework.incomplete} incomplete"
                )[:100],
            }
        )
    profile_lines = [f"• {item['label']}" for item in profiles]
    raw_profile = data.get("profile")
    profile = (
        TeacherStudentProfileDetail.model_validate(raw_profile)
        if isinstance(raw_profile, dict)
        else None
    )
    raw_preview = data.get("removal_preview")
    preview = (
        StudentRemovalPreview.model_validate(raw_preview)
        if isinstance(raw_preview, dict)
        else None
    )
    raw_result = data.get("removal_result")
    result = (
        StudentRemovalResult.model_validate(raw_result)
        if isinstance(raw_result, dict)
        else None
    )
    page_number = skip // PROFILE_PAGE_SIZE + 1
    page_count = max(1, (total + PROFILE_PAGE_SIZE - 1) // PROFILE_PAGE_SIZE)
    return {
        "feedback": feedback_block(
            error=str(data.get("error", "")),
            warning=str(data.get("warning", "")),
        ),
        "profiles": profiles,
        "profile_page": (
            f"Page {page_number} of {page_count} · {total} student(s)\n"
            + ("\n".join(profile_lines) or "No students on this list.")
        ),
        "list_title": "Former students (read-only)" if kind == "former" else "Students",
        "is_active_list": kind == "active",
        "has_previous": skip > 0,
        "has_next": skip + PROFILE_PAGE_SIZE < total,
        "profile_detail": _profile_detail_text(profile)
        if profile
        else "Select a student.",
        "can_remove": profile is not None and kind == "active",
        "can_view_homework": profile is not None,
        "removal_impact": _impact_text(preview) if preview else "No active preview.",
        "can_confirm": preview is not None
        and not data.get("submitting")
        and not data.get("outcome_unknown"),
        "can_retry": bool(data.get("outcome_unknown"))
        and isinstance(data.get("removal_request"), dict)
        and not data.get("submitting"),
        "can_cancel": preview is not None
        and not data.get("submitting")
        and not data.get("outcome_unknown"),
        "submitting": bool(data.get("submitting")),
        "result_text": _result_text(result)
        if result
        else "Removal result unavailable.",
        "has_result": result is not None,
        "can_return_to_former": kind == "former",
        "can_return_to_active": kind == "active",
    }


def build_student_profiles_dialog() -> Dialog:
    return Dialog(
        Window(
            Format("{feedback}<b>{list_title}</b>\n{profile_page}"),
            Select(
                Format("{item[label]}"),
                id="student_profile",
                item_id_getter=lambda item: item["id"],
                items="profiles",
                on_click=select_profile,
            ),
            Row(
                Button(
                    Const("Previous"),
                    id="previous",
                    on_click=previous_profiles,
                    when="has_previous",
                ),
                Button(
                    Const("Next"),
                    id="next",
                    on_click=next_profiles,
                    when="has_next",
                ),
            ),
            Row(
                Button(
                    Const("Former students"),
                    id="former",
                    on_click=show_former_students,
                    when="can_return_to_active",
                ),
                Button(
                    Const("Active students"),
                    id="active",
                    on_click=show_active_students,
                    when="can_return_to_former",
                ),
                Button(Const("Refresh"), id="refresh", on_click=refresh_profiles),
            ),
            Button(Const("Close"), id="close", on_click=close_student_profiles),
            state=StudentProfileScreens.active,
        ),
        Window(
            Format("{feedback}<b>{list_title}</b>\n{profile_page}"),
            Select(
                Format("{item[label]}"),
                id="former_student_profile",
                item_id_getter=lambda item: item["id"],
                items="profiles",
                on_click=select_profile,
            ),
            Row(
                Button(
                    Const("Previous"),
                    id="previous",
                    on_click=previous_profiles,
                    when="has_previous",
                ),
                Button(
                    Const("Next"),
                    id="next",
                    on_click=next_profiles,
                    when="has_next",
                ),
            ),
            Row(
                Button(
                    Const("Active students"),
                    id="active",
                    on_click=show_active_students,
                ),
                Button(Const("Refresh"), id="refresh", on_click=refresh_profiles),
            ),
            Button(Const("Close"), id="close", on_click=close_student_profiles),
            state=StudentProfileScreens.former,
        ),
        Window(
            Format("{feedback}{profile_detail}"),
            Button(
                Const("View homework"),
                id="homework",
                on_click=open_teacher_homework_for_student,
                when="can_view_homework",
            ),
            Button(
                Const("Remove student"),
                id="remove",
                on_click=begin_student_removal,
                when="can_remove",
            ),
            Row(
                Button(Const("Refresh"), id="refresh", on_click=refresh_profile),
                Button(
                    Const("Back to list"),
                    id="back",
                    on_click=back_to_profile_list,
                ),
            ),
            Button(Const("Close"), id="close", on_click=close_student_profiles),
            state=StudentProfileScreens.detail,
        ),
        Window(
            Format(
                "{feedback}{removal_impact}\n\n"
                "Confirmation preview expires after five minutes."
            ),
            Button(
                Const("Confirm removal"),
                id="confirm",
                on_click=confirm_student_removal,
                when="can_confirm",
            ),
            Button(
                Const("Retry same confirmation"),
                id="retry",
                on_click=retry_student_removal,
                when="can_retry",
            ),
            Button(
                Const("Cancel"),
                id="cancel",
                on_click=cancel_student_removal,
                when="can_cancel",
            ),
            state=StudentProfileScreens.removal,
        ),
        Window(
            Format("{feedback}{result_text}"),
            Button(
                Const("Former students"),
                id="former",
                on_click=show_former_students_after_removal,
            ),
            Button(Const("Close"), id="close", on_click=close_student_profiles),
            state=StudentProfileScreens.result,
        ),
        Window(
            Format("{feedback}Retry when the backend is available."),
            Button(Const("Retry"), id="retry", on_click=retry_profile_list),
            Button(Const("Close"), id="close", on_click=close_student_profiles),
            state=StudentProfileScreens.unavailable,
        ),
        getter=student_profile_data,
        on_start=student_profiles_started,
    )

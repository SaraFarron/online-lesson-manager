from html import escape
from typing import Any
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, DialogManager, ShowMode, StartMode, Window
from aiogram_dialog.widgets.kbd import Button, Row, Select
from aiogram_dialog.widgets.text import Const, Format

from telegram.api import (
    BackendError,
    BackendUnavailable,
    HomeworkAssignmentPage,
    HomeworkCompletionResult,
    HomeworkOutcomeUnknown,
    HomeworkSubmissionPage,
    SessionView,
    StudentPickerPage,
)
from telegram.dialogs.homework.text import (
    change_homework_text_page,
    homework_text_page,
)
from telegram.dialogs.homework.transfer import HomeworkDownloadFile
from telegram.shared.access import backend
from telegram.shared.feedback import feedback_block

STUDENT_PAGE_SIZE = 20
ASSIGNMENT_PAGE_SIZE = 20
SUBMISSION_PAGE_SIZE = 20


class TeacherHomeworkScreens(StatesGroup):
    students = State()
    assignments = State()
    detail = State()
    confirm_delete = State()
    unavailable = State()


async def open_teacher_homework_management(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework management requires a private chat")
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role != "TEACHER":
        raise ValueError("Only teachers can manage homework")
    data: dict[str, Any] = {
        "view": view.model_dump(mode="json"),
        "students": [],
        "student_total": 0,
        "student_skip": 0,
        "student_id": None,
        "student_label": "All students",
        "assignments": [],
        "assignment_total": 0,
        "assignment_skip": 0,
        "status_filter": None,
        "assignment": None,
        "materials": [],
        "submissions": [],
        "submission_total": 0,
        "submission_skip": 0,
        "selected_submission_id": None,
        "submitting": False,
        "outcome_unknown": False,
        "error": "",
        "warning": "",
        "notice": "",
        "assignment_text_page": 0,
        "submission_text_page": 0,
    }
    try:
        page = await backend(manager).read_student_picker(
            callback.from_user.id,
            callback.message.chat.id,
            limit=STUDENT_PAGE_SIZE,
        )
        _store_student_page(data, page, 0)
        state = TeacherHomeworkScreens.students
    except (BackendError, BackendUnavailable) as exc:
        data["error"] = str(exc)
        state = TeacherHomeworkScreens.unavailable
    await manager.start(
        state,
        data=data,
        mode=StartMode.NORMAL,
        show_mode=ShowMode.EDIT,
    )


async def teacher_homework_started(start_data: Any, manager: DialogManager) -> None:
    manager.dialog_data.clear()
    manager.dialog_data.update(start_data)


async def previous_assignment_text_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await change_homework_text_page(manager, "assignment_text_page", -1)


async def next_assignment_text_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await change_homework_text_page(manager, "assignment_text_page", 1)


async def previous_submission_text_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await change_homework_text_page(manager, "submission_text_page", -1)


async def next_submission_text_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await change_homework_text_page(manager, "submission_text_page", 1)


def _store_student_page(
    data: dict[str, Any], page: StudentPickerPage, skip: int
) -> None:
    data.update(
        students=[item.model_dump(mode="json") for item in page.items],
        student_total=page.total,
        student_skip=skip,
    )


def _store_assignment_page(
    data: dict[str, Any], page: HomeworkAssignmentPage, skip: int
) -> None:
    data.update(
        assignments=[item.model_dump(mode="json") for item in page.items],
        assignment_total=page.total,
        assignment_skip=skip,
    )


def _assignment_label(item: dict[str, Any]) -> str:
    student = item.get("student") or {}
    student_name = str(student.get("full_name") or "Student")
    text = " ".join(str(item.get("text") or "").split())
    if not text:
        text = "File assignment"
    elif len(text) > 56:
        text = f"{text[:53]}…"
    return (
        f"{escape(student_name)} — {escape(text)} "
        f"({escape(str(item['status']).lower())}, "
        f"{int(item.get('material_count', 0))} file(s))"
    )


async def teacher_homework_data(
    dialog_manager: DialogManager, **_: Any
) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    student_skip = int(data.get("student_skip", 0))
    student_total = int(data.get("student_total", 0))
    assignment_skip = int(data.get("assignment_skip", 0))
    assignment_total = int(data.get("assignment_total", 0))
    students = [
        {
            "id": str(item["id"]),
            "label": escape(str(item["display_label"])),
        }
        for item in data.get("students", [])
    ]
    assignments = [
        {"id": str(item["id"]), "label": _assignment_label(item)}
        for item in data.get("assignments", [])
    ]
    assignment = data.get("assignment")
    busy = bool(data.get("submitting"))
    completion_source = (
        str(assignment.get("completion_source") or "") if assignment else ""
    )
    status = str(assignment.get("status", "")) if assignment else ""
    if completion_source == "STUDENT_SUBMISSION":
        completion_label = "Completed by student submission."
    elif completion_source == "TEACHER":
        completion_label = "Marked complete by teacher."
    else:
        completion_label = "Incomplete."

    submissions = data.get("submissions", [])
    selected_submission = next(
        (
            item
            for item in submissions
            if str(item["id"]) == data.get("selected_submission_id")
        ),
        None,
    )
    version_options = [
        {
            "id": str(item["id"]),
            "label": (
                f"Version {int(item['version'])}"
                f"{' (current)' if item['is_current'] else ''}"
            ),
        }
        for item in submissions
    ]
    download_options: list[dict[str, str]] = []
    for material in data.get("materials", []):
        download_options.append(
            {
                "id": str(material["id"]),
                "label": (
                    f"Assignment: {escape(str(material['file_name']))} "
                    f"({int(material['byte_size'])} bytes)"
                ),
            }
        )
    if selected_submission is not None:
        for material in selected_submission.get("materials", []):
            download_options.append(
                {
                    "id": str(material["id"]),
                    "label": (
                        f"Solution v{int(selected_submission['version'])}: "
                        f"{escape(str(material['file_name']))} "
                        f"({int(material['byte_size'])} bytes)"
                    ),
                }
            )
    assignment_text_page = homework_text_page(
        str(assignment.get("text") or "") if assignment else None,
        int(data.get("assignment_text_page", 0)),
        label="Assignment text",
    )
    submission_text_page = homework_text_page(
        (
            str(selected_submission.get("text") or "(file-only solution)")
            if selected_submission
            else "(no submissions)"
        ),
        int(data.get("submission_text_page", 0)),
        label="Submission text",
    )
    return {
        "students": students,
        "student_page": (
            f"Page {student_skip // STUDENT_PAGE_SIZE + 1} of "
            f"{max(1, (student_total + STUDENT_PAGE_SIZE - 1) // STUDENT_PAGE_SIZE)} "
            f"({student_total} students)"
        ),
        "has_previous_students": student_skip > 0,
        "has_next_students": student_skip + len(students) < student_total,
        "student_label": escape(str(data.get("student_label") or "All students")),
        "assignments": assignments,
        "assignment_page": (
            f"Page {assignment_skip // ASSIGNMENT_PAGE_SIZE + 1} of "
            f"{max(1, (assignment_total + ASSIGNMENT_PAGE_SIZE - 1) // ASSIGNMENT_PAGE_SIZE)} "
            f"({assignment_total} assignments)"
        ),
        "has_previous_assignments": assignment_skip > 0,
        "has_next_assignments": assignment_skip + len(assignments) < assignment_total,
        "status_filter": data.get("status_filter") or "ALL",
        "assignment_text": assignment_text_page.content,
        "assignment_text_heading": assignment_text_page.heading,
        "has_previous_assignment_text_page": assignment_text_page.has_previous,
        "has_next_assignment_text_page": assignment_text_page.has_next,
        "detail_student_label": escape(
            str((assignment.get("student") or {}).get("full_name") or "Student")
        )
        if assignment
        else "",
        "assignment_status": status,
        "completion_label": completion_label,
        "submission_text": submission_text_page.content,
        "submission_text_heading": submission_text_page.heading,
        "has_previous_submission_text_page": submission_text_page.has_previous,
        "has_next_submission_text_page": submission_text_page.has_next,
        "submission_version": (
            f"Version {selected_submission['version']}"
            f"{' (current)' if selected_submission['is_current'] else ''}"
            if selected_submission
            else "(none)"
        ),
        "submission_total": int(data.get("submission_total", 0)),
        "versions": version_options,
        "has_versions": bool(version_options),
        "has_previous_versions": int(data.get("submission_skip", 0)) > 0,
        "has_next_versions": (
            int(data.get("submission_skip", 0)) + len(submissions)
            < int(data.get("submission_total", 0))
        ),
        "downloads": download_options,
        "has_downloads": bool(download_options),
        "can_complete": assignment is not None
        and status == "INCOMPLETE"
        and not busy
        and not data.get("outcome_unknown"),
        "can_delete": assignment is not None
        and not busy
        and not data.get("outcome_unknown"),
        "can_cancel_delete": not busy and not data.get("outcome_unknown"),
        "can_confirm_delete": assignment is not None
        and not busy
        and not data.get("outcome_unknown"),
        "can_check_delete": bool(data.get("outcome_unknown")) and not busy,
        "student_count": student_total,
        "feedback": feedback_block(
            error=str(data.get("error", "")) or None,
            warning=str(data.get("warning", "")) or None,
        ),
        "notice": escape(str(data.get("notice", ""))),
        "delete_warning": (
            "Delete this assignment and permanently remove its submissions "
            "and private files?"
            if assignment
            else "This assignment is no longer available."
        ),
    }


async def _load_student_page(
    sender_id: int,
    chat_id: int,
    manager: DialogManager,
    skip: int,
) -> bool:
    try:
        page = await backend(manager).read_student_picker(
            sender_id, chat_id, skip=skip, limit=STUDENT_PAGE_SIZE
        )
    except (BackendError, BackendUnavailable) as exc:
        manager.dialog_data["error"] = str(exc)
        return False
    _store_student_page(manager.dialog_data, page, skip)
    manager.dialog_data["error"] = ""
    return True


async def previous_students(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Student selection requires a private chat")
    skip = max(
        0,
        int(manager.dialog_data.get("student_skip", 0)) - STUDENT_PAGE_SIZE,
    )
    await _load_student_page(
        callback.from_user.id, callback.message.chat.id, manager, skip
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def next_students(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Student selection requires a private chat")
    skip = int(manager.dialog_data.get("student_skip", 0)) + STUDENT_PAGE_SIZE
    await _load_student_page(
        callback.from_user.id, callback.message.chat.id, manager, skip
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def select_student(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Student selection requires a private chat")
    selected = next(
        (
            entry
            for entry in manager.dialog_data.get("students", [])
            if str(entry["id"]) == item
        ),
        None,
    )
    if selected is None:
        manager.dialog_data["error"] = "Select a student from the current page."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    await _open_assignment_list(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        UUID(item),
        str(selected["display_label"]),
    )


async def select_all_students(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    await _open_assignment_list(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        None,
        "All students",
    )


async def _open_assignment_list(
    sender_id: int,
    chat_id: int,
    manager: DialogManager,
    student_id: UUID | None,
    student_label: str,
) -> None:
    data = manager.dialog_data
    data.update(
        student_id=str(student_id) if student_id else None,
        student_label=student_label,
        status_filter=None,
        assignment_skip=0,
        assignment=None,
        materials=[],
        submissions=[],
        submission_total=0,
        selected_submission_id=None,
        error="",
        notice="",
    )
    await _load_assignment_page(sender_id, chat_id, manager, 0)
    await manager.switch_to(TeacherHomeworkScreens.assignments, show_mode=ShowMode.EDIT)


async def _load_assignment_page(
    sender_id: int,
    chat_id: int,
    manager: DialogManager,
    skip: int,
) -> bool:
    data = manager.dialog_data
    try:
        page = await backend(manager).read_homework_assignments(
            sender_id,
            chat_id,
            skip=skip,
            limit=ASSIGNMENT_PAGE_SIZE,
            status=data.get("status_filter"),
            student_id=UUID(data["student_id"]) if data.get("student_id") else None,
        )
    except (BackendError, BackendUnavailable) as exc:
        data["error"] = str(exc)
        return False
    _store_assignment_page(data, page, skip)
    data.update(error="", notice="")
    return True


async def previous_assignments(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    skip = max(
        0,
        int(manager.dialog_data.get("assignment_skip", 0)) - ASSIGNMENT_PAGE_SIZE,
    )
    await _load_assignment_page(
        callback.from_user.id, callback.message.chat.id, manager, skip
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def next_assignments(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    skip = int(manager.dialog_data.get("assignment_skip", 0)) + ASSIGNMENT_PAGE_SIZE
    await _load_assignment_page(
        callback.from_user.id, callback.message.chat.id, manager, skip
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def _filter_status(
    callback: CallbackQuery,
    manager: DialogManager,
    status: str | None,
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    manager.dialog_data.update(status_filter=status, assignment_skip=0)
    await _load_assignment_page(
        callback.from_user.id, callback.message.chat.id, manager, 0
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def filter_all(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _filter_status(callback, manager, None)


async def filter_incomplete(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _filter_status(callback, manager, "INCOMPLETE")


async def filter_complete(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _filter_status(callback, manager, "COMPLETE")


async def refresh_assignment_list(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    await _load_assignment_page(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        int(manager.dialog_data.get("assignment_skip", 0)),
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def select_assignment(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Homework details require a private chat")
    if not any(
        str(entry["id"]) == item for entry in manager.dialog_data.get("assignments", [])
    ):
        manager.dialog_data["error"] = "Select an assignment from the current page."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    loaded = await _load_detail(
        callback.from_user.id, callback.message.chat.id, manager, UUID(item)
    )
    if loaded:
        await manager.switch_to(TeacherHomeworkScreens.detail, show_mode=ShowMode.EDIT)
    else:
        if manager.dialog_data.get("detail_missing"):
            await _load_assignment_page(
                callback.from_user.id,
                callback.message.chat.id,
                manager,
                int(manager.dialog_data.get("assignment_skip", 0)),
            )
            manager.dialog_data["notice"] = (
                "This assignment is no longer available. The list was refreshed."
            )
        await manager.switch_to(
            TeacherHomeworkScreens.assignments, show_mode=ShowMode.EDIT
        )


async def _load_detail(
    sender_id: int,
    chat_id: int,
    manager: DialogManager,
    assignment_id: UUID,
) -> bool:
    data = manager.dialog_data
    data["detail_missing"] = False
    try:
        detail = await backend(manager).read_homework_assignment(
            sender_id, chat_id, assignment_id
        )
    except BackendError as exc:
        data.update(
            error=str(exc),
            detail_missing=exc.status == 404,
        )
        if exc.status == 404:
            data.update(assignment=None, materials=[], submissions=[])
        return False
    except BackendUnavailable as exc:
        data["error"] = str(exc)
        return False

    data.update(
        assignment=detail.model_dump(mode="json"),
        materials=[material.model_dump(mode="json") for material in detail.materials],
        assignment_text_page=0,
        submission_text_page=0,
        error="",
        detail_missing=False,
        submission_skip=0,
        selected_submission_id=None,
    )
    try:
        page: HomeworkSubmissionPage = await backend(manager).read_homework_submissions(
            sender_id,
            chat_id,
            assignment_id,
            limit=SUBMISSION_PAGE_SIZE,
        )
    except BackendError as exc:
        if exc.status == 404:
            data.update(error=str(exc), detail_missing=True, assignment=None)
            return False
        data.update(submissions=[], submission_total=0, error=str(exc))
        return True
    except BackendUnavailable as exc:
        data.update(submissions=[], submission_total=0, error=str(exc))
        return True
    data.update(
        submissions=[submission.model_dump(mode="json") for submission in page.items],
        submission_total=page.total,
        submission_skip=0,
        selected_submission_id=str(page.items[0].id) if page.items else None,
        submission_text_page=0,
        error="",
    )
    return True


async def refresh_detail(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Refreshing homework requires a private chat")
    assignment = manager.dialog_data.get("assignment")
    if not isinstance(assignment, dict):
        return
    loaded = await _load_detail(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        UUID(assignment["id"]),
    )
    if loaded:
        manager.dialog_data.update(outcome_unknown=False, notice="")
        await manager.show(show_mode=ShowMode.EDIT)
    elif manager.dialog_data.get("detail_missing"):
        await _load_assignment_page(
            callback.from_user.id,
            callback.message.chat.id,
            manager,
            int(manager.dialog_data.get("assignment_skip", 0)),
        )
        manager.dialog_data["notice"] = "This assignment is no longer available."
        await manager.switch_to(
            TeacherHomeworkScreens.assignments, show_mode=ShowMode.EDIT
        )
    else:
        await manager.show(show_mode=ShowMode.EDIT)


async def select_submission_version(
    _callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    selected = next(
        (
            entry
            for entry in manager.dialog_data.get("submissions", [])
            if str(entry["id"]) == item
        ),
        None,
    )
    if selected is None:
        manager.dialog_data["error"] = "That submission version is not on this page."
    else:
        manager.dialog_data.update(
            selected_submission_id=item, submission_text_page=0, error=""
        )
    await manager.show(show_mode=ShowMode.EDIT)


async def previous_submission_versions(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_submission_page(callback, manager, -SUBMISSION_PAGE_SIZE)


async def next_submission_versions(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _load_submission_page(callback, manager, SUBMISSION_PAGE_SIZE)


async def _load_submission_page(
    callback: CallbackQuery, manager: DialogManager, offset: int
) -> None:
    if callback.message is None:
        raise ValueError("Submission history requires a private chat")
    data = manager.dialog_data
    assignment = data.get("assignment")
    if not isinstance(assignment, dict):
        return
    skip = max(0, int(data.get("submission_skip", 0)) + offset)
    try:
        page: HomeworkSubmissionPage = await backend(manager).read_homework_submissions(
            callback.from_user.id,
            callback.message.chat.id,
            UUID(assignment["id"]),
            skip=skip,
            limit=SUBMISSION_PAGE_SIZE,
        )
    except (BackendError, BackendUnavailable) as exc:
        data["error"] = str(exc)
    else:
        submissions = [item.model_dump(mode="json") for item in page.items]
        data.update(
            submissions=submissions,
            submission_total=page.total,
            submission_skip=skip,
            selected_submission_id=(str(submissions[0]["id"]) if submissions else None),
            error="",
        )
    await manager.show(show_mode=ShowMode.EDIT)


async def download_material(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Downloading homework requires a private chat")
    available = list(manager.dialog_data.get("materials", []))
    for submission in manager.dialog_data.get("submissions", []):
        available.extend(submission.get("materials", []))
    material = next((entry for entry in available if str(entry["id"]) == item), None)
    if material is None:
        manager.dialog_data["error"] = "That file is no longer available."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    try:
        bot = manager.middleware_data["bot"]
        if not isinstance(bot, Bot):
            raise TypeError("Telegram bot is not configured")
        await bot.send_document(
            chat_id=callback.message.chat.id,
            document=HomeworkDownloadFile(
                backend(manager),
                callback.from_user.id,
                callback.message.chat.id,
                UUID(item),
                str(material["file_name"]),
            ),
        )
    except (BackendError, BackendUnavailable) as exc:
        manager.dialog_data["error"] = str(exc)
    except TelegramAPIError as exc:
        manager.dialog_data["error"] = f"Telegram could not send this file: {exc}"
    else:
        manager.dialog_data.update(
            error="", notice=f"Sent {material['file_name']} as a separate document."
        )
    await manager.show(show_mode=ShowMode.EDIT)


async def mark_complete(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Completing homework requires a private chat")
    data = manager.dialog_data
    assignment = data.get("assignment")
    if (
        not isinstance(assignment, dict)
        or assignment["status"] != "INCOMPLETE"
        or data.get("submitting")
        or data.get("outcome_unknown")
    ):
        return
    assignment_id = UUID(assignment["id"])
    data.update(submitting=True, error="", warning="", notice="")
    await manager.show(show_mode=ShowMode.EDIT)
    try:
        result: HomeworkCompletionResult = await backend(manager).complete_homework(
            callback.from_user.id, callback.message.chat.id, assignment_id
        )
    except HomeworkOutcomeUnknown as exc:
        data.update(submitting=False, outcome_unknown=True, error="", warning=str(exc))
        loaded = await _load_detail(
            callback.from_user.id, callback.message.chat.id, manager, assignment_id
        )
        if loaded:
            current = data["assignment"]
            data["outcome_unknown"] = False
            if (
                current["status"] == "COMPLETE"
                and current["completion_source"] == "TEACHER"
            ):
                data.update(
                    warning="",
                    notice="Teacher completion was confirmed after reloading.",
                )
            else:
                data["warning"] = (
                    "Completion was not confirmed. Review the current state before retrying."
                )
        elif data.get("detail_missing"):
            await _load_assignment_page(
                callback.from_user.id,
                callback.message.chat.id,
                manager,
                int(data.get("assignment_skip", 0)),
            )
            data["warning"] = ""
            data["notice"] = "This assignment is no longer available."
            await manager.switch_to(
                TeacherHomeworkScreens.assignments, show_mode=ShowMode.EDIT
            )
            return
    except (BackendError, BackendUnavailable) as exc:
        data.update(submitting=False, error=str(exc), warning="")
        loaded = await _load_detail(
            callback.from_user.id, callback.message.chat.id, manager, assignment_id
        )
        if loaded:
            data["error"] = str(exc)
        elif data.get("detail_missing"):
            await _load_assignment_page(
                callback.from_user.id,
                callback.message.chat.id,
                manager,
                int(data.get("assignment_skip", 0)),
            )
            data["notice"] = "This assignment is no longer available."
            await manager.switch_to(
                TeacherHomeworkScreens.assignments, show_mode=ShowMode.EDIT
            )
            return
    else:
        data.update(submitting=False, outcome_unknown=False, warning="")
        assignment.update(
            status=result.status,
            completion_source=result.completion_source,
            completed_at=result.completed_at.isoformat(),
        )
        data["assignment"] = assignment
        data["notice"] = (
            "Marked complete by teacher."
            if result.completion_source == "TEACHER"
            else f"Current completion: {result.completion_source.lower()}."
        )
        loaded = await _load_detail(
            callback.from_user.id, callback.message.chat.id, manager, assignment_id
        )
        if not loaded and data.get("detail_missing"):
            await _load_assignment_page(
                callback.from_user.id,
                callback.message.chat.id,
                manager,
                int(data.get("assignment_skip", 0)),
            )
            data["notice"] = "This assignment is no longer available."
            await manager.switch_to(
                TeacherHomeworkScreens.assignments, show_mode=ShowMode.EDIT
            )
            return
        data["notice"] = (
            "Marked complete by teacher."
            if result.completion_source == "TEACHER"
            else f"Current completion: {result.completion_source.lower()}."
        )
    await manager.show(show_mode=ShowMode.EDIT)


async def begin_delete(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data.update(error="", warning="", notice="", outcome_unknown=False)
    await manager.switch_to(
        TeacherHomeworkScreens.confirm_delete, show_mode=ShowMode.EDIT
    )


async def cancel_delete(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        return
    manager.dialog_data.update(error="", warning="", outcome_unknown=False)
    await manager.switch_to(TeacherHomeworkScreens.detail, show_mode=ShowMode.EDIT)


async def _return_to_assignment_list(
    callback: CallbackQuery,
    manager: DialogManager,
    *,
    notice: str,
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    data = manager.dialog_data
    assignment = data.get("assignment")
    if isinstance(assignment, dict):
        data["assignments"] = [
            item
            for item in data.get("assignments", [])
            if item["id"] != assignment["id"]
        ]
        data["assignment_total"] = max(0, int(data.get("assignment_total", 0)) - 1)
    data.update(
        assignment=None,
        materials=[],
        submissions=[],
        error="",
        warning="",
        notice=notice,
    )
    await _load_assignment_page(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        int(data.get("assignment_skip", 0)),
    )
    data["notice"] = notice
    await manager.switch_to(TeacherHomeworkScreens.assignments, show_mode=ShowMode.EDIT)


async def back_to_assignment_list(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    await _load_assignment_page(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        int(manager.dialog_data.get("assignment_skip", 0)),
    )
    await manager.switch_to(TeacherHomeworkScreens.assignments, show_mode=ShowMode.EDIT)


async def _check_delete_outcome(
    callback: CallbackQuery, manager: DialogManager, assignment_id: UUID
) -> None:
    if callback.message is None:
        raise ValueError("Checking deletion requires a private chat")
    data = manager.dialog_data
    try:
        detail = await backend(manager).read_homework_assignment(
            callback.from_user.id, callback.message.chat.id, assignment_id
        )
    except BackendError as exc:
        if exc.status == 404:
            await _return_to_assignment_list(
                callback,
                manager,
                notice="Deletion was confirmed after refreshing the assignment.",
            )
            return
        data.update(
            submitting=False,
            outcome_unknown=True,
            error="",
            warning=f"{exc} Check the assignment status before retrying deletion.",
        )
    except BackendUnavailable as exc:
        data.update(
            submitting=False,
            outcome_unknown=True,
            error="",
            warning=f"{exc} Check the assignment status before retrying deletion.",
        )
    else:
        data.update(
            assignment=detail.model_dump(mode="json"),
            materials=[item.model_dump(mode="json") for item in detail.materials],
            submitting=False,
            outcome_unknown=False,
            error="",
            warning=(
                "The assignment still exists. Review its current state before "
                "confirming deletion again."
            ),
        )
    await manager.show(show_mode=ShowMode.EDIT)


async def confirm_delete(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Deleting homework requires a private chat")
    data = manager.dialog_data
    assignment = data.get("assignment")
    if (
        not isinstance(assignment, dict)
        or data.get("submitting")
        or data.get("outcome_unknown")
    ):
        return
    assignment_id = UUID(assignment["id"])
    data.update(submitting=True, error="", warning="", notice="")
    await manager.show(show_mode=ShowMode.EDIT)
    try:
        await backend(manager).delete_homework_assignment(
            callback.from_user.id, callback.message.chat.id, assignment_id
        )
    except HomeworkOutcomeUnknown as exc:
        data.update(submitting=False, outcome_unknown=True, error="", warning=str(exc))
        await _check_delete_outcome(callback, manager, assignment_id)
        return
    except BackendError as exc:
        if exc.status == 404:
            data.update(submitting=False, outcome_unknown=False)
            await _return_to_assignment_list(
                callback,
                manager,
                notice="This assignment was already unavailable; the list was refreshed.",
            )
            return
        data.update(submitting=False, error=str(exc))
    except BackendUnavailable as exc:
        data.update(submitting=False, error=str(exc))
    else:
        data.update(submitting=False, outcome_unknown=False)
        await _return_to_assignment_list(
            callback, manager, notice="Assignment and its files were deleted."
        )
        return
    await manager.show(show_mode=ShowMode.EDIT)


async def check_delete_status(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Checking deletion requires a private chat")
    assignment = manager.dialog_data.get("assignment")
    if isinstance(assignment, dict):
        await _check_delete_outcome(callback, manager, UUID(assignment["id"]))


async def retry_management(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework management requires a private chat")
    page_loaded = await _load_student_page(
        callback.from_user.id, callback.message.chat.id, manager, 0
    )
    if page_loaded:
        await manager.switch_to(
            TeacherHomeworkScreens.students, show_mode=ShowMode.EDIT
        )
    else:
        await manager.show(show_mode=ShowMode.EDIT)


async def close_management(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.done(show_mode=ShowMode.EDIT)


def build_homework_teacher_management_dialog() -> Dialog:
    return Dialog(
        Window(
            Format(
                "{feedback}Choose a student to manage homework, or view all students.\n"
                "{student_page}"
            ),
            Select(
                Format("{item[label]}"),
                id="student",
                item_id_getter=lambda item: item["id"],
                items="students",
                on_click=select_student,
            ),
            Button(
                Const("All students"),
                id="all_students",
                on_click=select_all_students,
            ),
            Row(
                Button(
                    Const("Previous"),
                    id="previous_students",
                    on_click=previous_students,
                    when="has_previous_students",
                ),
                Button(
                    Const("Next"),
                    id="next_students",
                    on_click=next_students,
                    when="has_next_students",
                ),
            ),
            Button(Const("Close"), id="close", on_click=close_management),
            state=TeacherHomeworkScreens.students,
        ),
        Window(
            Format(
                "{feedback}Homework for {student_label} — {status_filter}\n"
                "{assignment_page}\n{notice}"
            ),
            Select(
                Format("{item[label]}"),
                id="assignment",
                item_id_getter=lambda item: item["id"],
                items="assignments",
                on_click=select_assignment,
            ),
            Row(
                Button(Const("All"), id="all", on_click=filter_all),
                Button(
                    Const("Incomplete"), id="incomplete", on_click=filter_incomplete
                ),
                Button(Const("Complete"), id="complete", on_click=filter_complete),
            ),
            Row(
                Button(
                    Const("Previous"),
                    id="previous_assignments",
                    on_click=previous_assignments,
                    when="has_previous_assignments",
                ),
                Button(
                    Const("Next"),
                    id="next_assignments",
                    on_click=next_assignments,
                    when="has_next_assignments",
                ),
            ),
            Row(
                Button(
                    Const("Change student"),
                    id="change_student",
                    on_click=retry_management,
                ),
                Button(
                    Const("Refresh"), id="refresh", on_click=refresh_assignment_list
                ),
            ),
            Button(Const("Close"), id="close", on_click=close_management),
            state=TeacherHomeworkScreens.assignments,
        ),
        Window(
            Format(
                "{feedback}{detail_student_label}\n"
                "{assignment_text_heading}{assignment_text}\n"
                "Status: {assignment_status}\n{completion_label}\n"
                "Submission: {submission_version}\n"
                "{submission_text_heading}{submission_text}\n{notice}"
            ),
            Row(
                Button(
                    Const("Previous assignment text"),
                    id="previous_assignment_text",
                    on_click=previous_assignment_text_page,
                    when="has_previous_assignment_text_page",
                ),
                Button(
                    Const("Next assignment text"),
                    id="next_assignment_text",
                    on_click=next_assignment_text_page,
                    when="has_next_assignment_text_page",
                ),
            ),
            Row(
                Button(
                    Const("Previous submission text"),
                    id="previous_submission_text",
                    on_click=previous_submission_text_page,
                    when="has_previous_submission_text_page",
                ),
                Button(
                    Const("Next submission text"),
                    id="next_submission_text",
                    on_click=next_submission_text_page,
                    when="has_next_submission_text_page",
                ),
            ),
            Select(
                Format("{item[label]}"),
                id="submission_version",
                item_id_getter=lambda item: item["id"],
                items="versions",
                on_click=select_submission_version,
                when="has_versions",
            ),
            Row(
                Button(
                    Const("Earlier versions"),
                    id="previous_versions",
                    on_click=previous_submission_versions,
                    when="has_previous_versions",
                ),
                Button(
                    Const("Later versions"),
                    id="next_versions",
                    on_click=next_submission_versions,
                    when="has_next_versions",
                ),
            ),
            Select(
                Format("{item[label]}"),
                id="download",
                item_id_getter=lambda item: item["id"],
                items="downloads",
                on_click=download_material,
                when="has_downloads",
            ),
            Row(
                Button(
                    Const("Mark complete"),
                    id="complete",
                    on_click=mark_complete,
                    when="can_complete",
                ),
                Button(
                    Const("Delete assignment"),
                    id="delete",
                    on_click=begin_delete,
                    when="can_delete",
                ),
            ),
            Row(
                Button(
                    Const("Refresh"),
                    id="refresh",
                    on_click=refresh_detail,
                ),
                Button(
                    Const("Back to list"), id="back", on_click=back_to_assignment_list
                ),
            ),
            state=TeacherHomeworkScreens.detail,
        ),
        Window(
            Format(
                "{feedback}{assignment_text_heading}{assignment_text}\n{delete_warning}"
            ),
            Row(
                Button(
                    Const("Previous assignment text"),
                    id="previous_assignment_text",
                    on_click=previous_assignment_text_page,
                    when="has_previous_assignment_text_page",
                ),
                Button(
                    Const("Next assignment text"),
                    id="next_assignment_text",
                    on_click=next_assignment_text_page,
                    when="has_next_assignment_text_page",
                ),
            ),
            Button(
                Const("Confirm delete"),
                id="confirm",
                on_click=confirm_delete,
                when="can_confirm_delete",
            ),
            Button(
                Const("Check status"),
                id="check_status",
                on_click=check_delete_status,
                when="can_check_delete",
            ),
            Button(
                Const("Cancel"),
                id="cancel",
                on_click=cancel_delete,
                when="can_cancel_delete",
            ),
            state=TeacherHomeworkScreens.confirm_delete,
        ),
        Window(
            Format("{feedback}Retry when the backend is available."),
            Button(Const("Retry"), id="retry", on_click=retry_management),
            Button(Const("Close"), id="close", on_click=close_management),
            state=TeacherHomeworkScreens.unavailable,
        ),
        getter=teacher_homework_data,
        on_start=teacher_homework_started,
    )

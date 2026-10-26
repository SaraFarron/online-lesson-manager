from collections.abc import AsyncIterable
from html import escape
from typing import Any
from uuid import UUID, uuid4

from aiogram import Bot
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, ShowMode, StartMode, Window
from aiogram_dialog.widgets.input import ManagedTextInput, MessageInput, TextInput
from aiogram_dialog.widgets.kbd import Button, Column, Row, Select
from aiogram_dialog.widgets.text import Const, Format

from telegram.api import (
    MAX_HOMEWORK_ATTACHMENT_BYTES,
    MAX_HOMEWORK_ATTACHMENT_TOTAL_BYTES,
    MAX_HOMEWORK_ATTACHMENTS,
    BackendError,
    BackendUnavailable,
    HomeworkAssignmentPage,
    HomeworkAttachmentTooLarge,
    HomeworkOutcomeUnknown,
    HomeworkSubmissionPayload,
    HomeworkSubmissionResult,
    SessionView,
    StagedHomeworkAttachment,
)
from telegram.homework_transfer import HomeworkDownloadFile
from telegram.onboarding import backend

ASSIGNMENT_PAGE_SIZE = 20


class StudentHomeworkScreens(StatesGroup):
    assignments = State()
    detail = State()
    compose = State()
    review = State()
    unavailable = State()


async def open_student_homework(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Student homework requires a private chat")
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role != "STUDENT":
        raise ValueError("Only students can open their homework")

    data: dict[str, Any] = {
        "view": view.model_dump(mode="json"),
        "assignments": [],
        "total": 0,
        "skip": 0,
        "status_filter": None,
        "assignment": None,
        "materials": [],
        "attachments": [],
        "text": None,
        "pending_upload": None,
        "uploading": False,
        "upload_outcome_unknown": False,
        "submitting": False,
        "operation_key": None,
        "publish_payload": None,
        "outcome_unknown": False,
        "notice": "",
        "error": "",
    }
    try:
        page = await backend(manager).read_homework_assignments(
            callback.from_user.id,
            callback.message.chat.id,
            limit=ASSIGNMENT_PAGE_SIZE,
        )
        _store_assignment_page(data, page, 0)
        state = StudentHomeworkScreens.assignments
    except (BackendError, BackendUnavailable) as exc:
        data["error"] = str(exc)
        state = StudentHomeworkScreens.unavailable
    await manager.start(
        state,
        data=data,
        mode=StartMode.NORMAL,
        show_mode=ShowMode.EDIT,
    )


async def student_homework_started(start_data: Any, manager: DialogManager) -> None:
    manager.dialog_data.clear()
    manager.dialog_data.update(start_data)


def _store_assignment_page(
    data: dict[str, Any], page: HomeworkAssignmentPage, skip: int
) -> None:
    data.update(
        assignments=[item.model_dump(mode="json") for item in page.items],
        total=page.total,
        skip=skip,
    )


async def student_homework_data(
    dialog_manager: DialogManager, **_: Any
) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    skip = int(data.get("skip", 0))
    total = int(data.get("total", 0))
    page_count = max(1, (total + ASSIGNMENT_PAGE_SIZE - 1) // ASSIGNMENT_PAGE_SIZE)
    assignments = [
        {
            "id": str(item["id"]),
            "label": _assignment_label(item),
        }
        for item in data.get("assignments", [])
    ]
    assignment = data.get("assignment")
    status = str(assignment.get("status", "")) if assignment else ""
    completion_source = (
        str(assignment.get("completion_source") or "") if assignment else ""
    )
    if completion_source == "TEACHER":
        completion_label = "Marked complete by your teacher; no further submissions."
    elif completion_source == "STUDENT_SUBMISSION":
        completion_label = (
            "Completed by your submission. You may submit another version; "
            "the assignment stays complete."
        )
    else:
        completion_label = (
            "Incomplete. A successful submission completes this assignment."
        )
    materials = [
        {
            "id": str(item["id"]),
            "label": f"Download {escape(str(item['file_name']))} "
            f"({int(item['byte_size'])} bytes)",
        }
        for item in data.get("materials", [])
    ]
    attachments = [
        {
            "id": str(item["id"]),
            "label": f"Remove {escape(str(item['file_name']))}",
        }
        for item in data.get("attachments", [])
    ]
    pending_upload = data.get("pending_upload")
    busy = bool(data.get("submitting") or data.get("uploading"))
    return {
        "assignments": assignments,
        "page": f"Page {skip // ASSIGNMENT_PAGE_SIZE + 1} of {page_count} ({total} assignments)",
        "has_previous": skip > 0,
        "has_next": skip + len(assignments) < total,
        "status_filter": data.get("status_filter") or "ALL",
        "error": escape(str(data.get("error", ""))),
        "notice": escape(str(data.get("notice", ""))),
        "has_assignment": assignment is not None,
        "assignment_text": (
            escape(str(assignment.get("text") or "(no text)")) if assignment else ""
        ),
        "assignment_status": status,
        "completion_label": completion_label,
        "materials": materials,
        "has_materials": bool(materials),
        "can_submit": assignment is not None
        and completion_source != "TEACHER"
        and not busy
        and not data.get("outcome_unknown"),
        "text": escape(str(data.get("text") or "")) or "(no text)",
        "attachments": attachments,
        "has_attachments": bool(attachments),
        "attachment_summary": (
            "\n".join(
                f"{escape(str(item['file_name']))} ({int(item['byte_size'])} bytes)"
                for item in data.get("attachments", [])
            )
            or "(no files)"
        ),
        "can_review": bool(str(data.get("text") or "").strip() or attachments)
        and not pending_upload
        and not busy
        and not data.get("outcome_unknown"),
        "can_finish": bool(str(data.get("text") or "").strip() or attachments)
        and not pending_upload
        and not busy,
        "can_cancel": not pending_upload
        and not busy
        and not data.get("outcome_unknown"),
        "can_edit": not bool(data.get("outcome_unknown")) and not busy,
        "has_text": bool(data.get("text")),
        "upload_status": (
            f"Uploading {escape(str(pending_upload.get('file_name', 'file')))}."
            if data.get("uploading") and isinstance(pending_upload, dict)
            else "Upload result unknown; retry this same file to reconcile it."
            if data.get("upload_outcome_unknown")
            else ""
        ),
        "pending_file_name": escape(
            str(pending_upload.get("file_name", "file")) if pending_upload else "file"
        ),
        "upload_unknown": bool(data.get("upload_outcome_unknown")),
        "outcome_unknown": bool(data.get("outcome_unknown")),
        "submit_label": (
            "Retry Finish (same operation)"
            if data.get("outcome_unknown")
            else "Finish submission"
        ),
        "retry_label": (
            "Submit another version"
            if completion_source == "STUDENT_SUBMISSION"
            else "Submit solution"
        ),
    }


def _assignment_label(item: dict[str, Any]) -> str:
    text = " ".join(str(item.get("text") or "").split())
    if not text:
        text = "File assignment"
    elif len(text) > 72:
        text = f"{text[:69]}…"
    count = int(item.get("material_count", 0))
    return f"{escape(text)} — {escape(str(item['status']).lower())}, {count} file(s)"


async def _load_assignments(
    sender_id: int, chat_id: int, manager: DialogManager, skip: int
) -> None:
    data = manager.dialog_data
    try:
        page = await backend(manager).read_homework_assignments(
            sender_id,
            chat_id,
            skip=skip,
            limit=ASSIGNMENT_PAGE_SIZE,
            status=data.get("status_filter"),
        )
    except (BackendError, BackendUnavailable) as exc:
        data["error"] = str(exc)
    else:
        _store_assignment_page(data, page, skip)
        data.update(error="", notice="")
    await manager.show(show_mode=ShowMode.EDIT)


async def previous_assignments(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    await _load_assignments(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        max(0, int(manager.dialog_data.get("skip", 0)) - ASSIGNMENT_PAGE_SIZE),
    )


async def next_assignments(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    await _load_assignments(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        int(manager.dialog_data.get("skip", 0)) + ASSIGNMENT_PAGE_SIZE,
    )


async def filter_all(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _set_status_filter(callback, manager, None)


async def filter_incomplete(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _set_status_filter(callback, manager, "INCOMPLETE")


async def filter_complete(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _set_status_filter(callback, manager, "COMPLETE")


async def _set_status_filter(
    callback: CallbackQuery,
    manager: DialogManager,
    status: str | None,
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    manager.dialog_data.update(status_filter=status, skip=0)
    await _load_assignments(callback.from_user.id, callback.message.chat.id, manager, 0)


async def select_assignment(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Homework details require a private chat")
    if not any(
        str(entry["id"]) == item for entry in manager.dialog_data["assignments"]
    ):
        manager.dialog_data["error"] = "Select an assignment from the current page."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    await _load_assignment_detail(
        callback.from_user.id, callback.message.chat.id, manager, UUID(item)
    )


async def _load_assignment_detail(
    sender_id: int,
    chat_id: int,
    manager: DialogManager,
    assignment_id: UUID,
) -> None:
    data = manager.dialog_data
    try:
        detail = await backend(manager).read_homework_assignment(
            sender_id, chat_id, assignment_id
        )
    except BackendError as exc:
        if exc.status == 404:
            try:
                page = await backend(manager).read_homework_assignments(
                    sender_id,
                    chat_id,
                    skip=int(data.get("skip", 0)),
                    limit=ASSIGNMENT_PAGE_SIZE,
                    status=data.get("status_filter"),
                )
            except (BackendError, BackendUnavailable) as refresh_exc:
                data.update(
                    assignment=None,
                    materials=[],
                    error=str(refresh_exc),
                    notice="",
                )
            else:
                _store_assignment_page(data, page, int(data.get("skip", 0)))
                data.update(
                    assignment=None,
                    materials=[],
                    error="This homework is deleted or no longer available.",
                    notice="",
                )
        else:
            data.update(assignment=None, materials=[], error=str(exc), notice="")
        await manager.switch_to(
            StudentHomeworkScreens.assignments, show_mode=ShowMode.EDIT
        )
        return
    except BackendUnavailable as exc:
        data.update(assignment=None, materials=[], error=str(exc), notice="")
        await manager.switch_to(
            StudentHomeworkScreens.assignments, show_mode=ShowMode.EDIT
        )
        return
    data.update(
        assignment=detail.model_dump(mode="json"),
        materials=[item.model_dump(mode="json") for item in detail.materials],
        error="",
        notice="",
    )
    await manager.switch_to(StudentHomeworkScreens.detail, show_mode=ShowMode.EDIT)


async def refresh_assignment(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Refreshing homework requires a private chat")
    assignment = manager.dialog_data.get("assignment")
    if not isinstance(assignment, dict):
        return
    await _load_assignment_detail(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        UUID(assignment["id"]),
    )


async def back_to_assignments(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    manager.dialog_data.update(assignment=None, materials=[], error="", notice="")
    await manager.switch_to(StudentHomeworkScreens.assignments, show_mode=ShowMode.EDIT)


async def download_material(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Downloading homework requires a private chat")
    material = next(
        (
            entry
            for entry in manager.dialog_data.get("materials", [])
            if str(entry["id"]) == item
        ),
        None,
    )
    if material is None:
        manager.dialog_data["error"] = "That material is no longer available."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    client = backend(manager)
    try:
        bot = manager.middleware_data["bot"]
        if not isinstance(bot, Bot):
            raise TypeError("Telegram bot is not configured")
        await bot.send_document(
            chat_id=callback.message.chat.id,
            document=HomeworkDownloadFile(
                client,
                callback.from_user.id,
                callback.message.chat.id,
                UUID(item),
                str(material["file_name"]),
            ),
        )
    except (BackendError, BackendUnavailable, HomeworkAttachmentTooLarge) as exc:
        manager.dialog_data["error"] = str(exc)
    except TelegramAPIError as exc:
        manager.dialog_data["error"] = f"Telegram could not send this material: {exc}"
    else:
        manager.dialog_data.update(
            error="", notice=f"Sent {material['file_name']} as a separate document."
        )
    await manager.show(show_mode=ShowMode.EDIT)


async def begin_submission(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    assignment = manager.dialog_data.get("assignment")
    if not isinstance(assignment, dict):
        return
    if assignment.get("completion_source") == "TEACHER" or manager.dialog_data.get(
        "outcome_unknown"
    ):
        return
    manager.dialog_data.update(
        text=None,
        attachments=[],
        pending_upload=None,
        uploading=False,
        upload_outcome_unknown=False,
        operation_key=None,
        publish_payload=None,
        submitting=False,
        outcome_unknown=False,
        error="",
        notice="",
    )
    await manager.switch_to(StudentHomeworkScreens.compose, show_mode=ShowMode.EDIT)


async def set_solution_text(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        return
    text = value.strip()
    if not text:
        manager.dialog_data["error"] = "Solution text cannot be blank."
    else:
        manager.dialog_data.update(text=text, error="", operation_key=None)
    await manager.show(show_mode=ShowMode.EDIT)


async def clear_solution_text(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        return
    manager.dialog_data.update(text=None, error="", operation_key=None)
    await manager.show(show_mode=ShowMode.EDIT)


async def receive_solution_document(
    message: Message, _widget: MessageInput, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        return
    if message.from_user is None or message.document is None:
        raise ValueError("Solution upload requires a Telegram document")
    attachments = manager.dialog_data.get("attachments", [])
    if len(attachments) >= MAX_HOMEWORK_ATTACHMENTS:
        manager.dialog_data["error"] = (
            f"A solution can contain at most {MAX_HOMEWORK_ATTACHMENTS} files."
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    total_size = sum(int(item["byte_size"]) for item in attachments)
    advertised_size = message.document.file_size
    if advertised_size is not None and (
        advertised_size > MAX_HOMEWORK_ATTACHMENT_BYTES
        or total_size + advertised_size > MAX_HOMEWORK_ATTACHMENT_TOTAL_BYTES
    ):
        manager.dialog_data["error"] = (
            "The file or solution exceeds the 20 MiB per-file or 50 MiB total limit."
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(
        pending_upload={
            "telegram_file_id": message.document.file_id,
            "file_name": message.document.file_name or "solution",
            "advertised_size": advertised_size,
            "operation_key": str(uuid4()),
        },
        upload_outcome_unknown=False,
        error="",
    )
    await _upload_pending_solution(message.from_user.id, message.chat.id, manager)


async def retry_solution_upload(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Solution upload requires a private chat")
    await _upload_pending_solution(
        callback.from_user.id, callback.message.chat.id, manager
    )


async def _upload_pending_solution(
    sender_id: int, chat_id: int, manager: DialogManager
) -> None:
    data = manager.dialog_data
    pending = data.get("pending_upload")
    if not pending or data.get("uploading"):
        return
    data.update(uploading=True, error="")
    await manager.show(show_mode=ShowMode.EDIT)
    try:
        bot = manager.middleware_data["bot"]
        if not isinstance(bot, Bot):
            raise TypeError("Telegram bot is not configured")
        telegram_file = await bot.get_file(pending["telegram_file_id"])
        if telegram_file.file_path is None:
            data.update(
                pending_upload=None,
                uploading=False,
                upload_outcome_unknown=False,
                error="Telegram could not provide a download path. Send the file again.",
            )
            await manager.show(show_mode=ShowMode.EDIT)
            return
        file_url = bot.session.api.file_url(bot.token, telegram_file.file_path)

        def source_factory() -> AsyncIterable[bytes]:
            return bot.session.stream_content(
                url=file_url, timeout=120, chunk_size=65536
            )

        staged: StagedHomeworkAttachment = await backend(
            manager
        ).upload_homework_attachment(
            sender_id,
            chat_id,
            pending["file_name"],
            source_factory,
            UUID(pending["operation_key"]),
            advertised_size=pending["advertised_size"],
        )
    except HomeworkOutcomeUnknown as exc:
        data.update(
            uploading=False,
            upload_outcome_unknown=True,
            error=f"{exc} Retry this same file to reconcile it.",
        )
    except (BackendError, BackendUnavailable, HomeworkAttachmentTooLarge) as exc:
        data.update(
            pending_upload=None,
            uploading=False,
            upload_outcome_unknown=False,
            error=str(exc),
        )
    except TelegramAPIError as exc:
        data.update(
            pending_upload=None,
            uploading=False,
            upload_outcome_unknown=False,
            error=f"Telegram could not retrieve this file: {exc}",
        )
    else:
        attachments = data.get("attachments", [])
        attachments.append(staged.model_dump(mode="json"))
        data.update(
            attachments=attachments,
            pending_upload=None,
            uploading=False,
            upload_outcome_unknown=False,
            operation_key=None,
            error="",
        )
        total_size = sum(int(item["byte_size"]) for item in attachments)
        if total_size > MAX_HOMEWORK_ATTACHMENT_TOTAL_BYTES:
            data["error"] = (
                "This puts the solution over the 50 MiB total limit. "
                "Remove a file before finishing."
            )
    await manager.show(show_mode=ShowMode.EDIT)


async def remove_solution_attachment(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Removing a solution file requires a private chat")
    if manager.dialog_data.get("outcome_unknown"):
        return
    attachments = manager.dialog_data.get("attachments", [])
    selected = next((entry for entry in attachments if str(entry["id"]) == item), None)
    if selected is None:
        manager.dialog_data["error"] = "That draft file is no longer available."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    try:
        await backend(manager).delete_staged_homework_attachment(
            callback.from_user.id, callback.message.chat.id, UUID(item)
        )
    except BackendError as exc:
        if exc.status != 404:
            manager.dialog_data["error"] = str(exc)
            await manager.show(show_mode=ShowMode.EDIT)
            return
    except (BackendUnavailable, HomeworkOutcomeUnknown) as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    attachments.remove(selected)
    manager.dialog_data.update(attachments=attachments, error="", operation_key=None)
    await manager.show(show_mode=ShowMode.EDIT)


async def review_submission(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    if data.get("outcome_unknown") or data.get("pending_upload"):
        return
    if not str(data.get("text") or "").strip() and not data.get("attachments"):
        data["error"] = "Add solution text or at least one file before reviewing."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    total_size = sum(int(item["byte_size"]) for item in data.get("attachments", []))
    if total_size > MAX_HOMEWORK_ATTACHMENT_TOTAL_BYTES:
        data["error"] = "Remove files until the solution is within the 50 MiB limit."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data["error"] = ""
    await manager.switch_to(StudentHomeworkScreens.review, show_mode=ShowMode.EDIT)


async def return_to_compose(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        return
    await manager.switch_to(StudentHomeworkScreens.compose, show_mode=ShowMode.EDIT)


async def finish_submission(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Finishing a solution requires a private chat")
    data = manager.dialog_data
    if data.get("submitting"):
        return
    if data.get("pending_upload"):
        data["error"] = "Finish or retry the pending file upload before submitting."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    assignment = data.get("assignment")
    if not isinstance(assignment, dict):
        data["error"] = "This assignment is no longer available."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    if data.get("outcome_unknown"):
        payload_data = data.get("publish_payload")
        if not isinstance(payload_data, dict):
            data["error"] = "The pending submission cannot be reconciled."
            await manager.show(show_mode=ShowMode.EDIT)
            return
        try:
            payload = HomeworkSubmissionPayload.model_validate(payload_data)
        except ValueError as exc:
            data["error"] = f"The pending submission is invalid: {exc}"
            await manager.show(show_mode=ShowMode.EDIT)
            return
    else:
        try:
            payload = HomeworkSubmissionPayload(
                text=str(data.get("text") or "").strip() or None,
                attachment_ids=[
                    UUID(item["id"]) for item in data.get("attachments", [])
                ],
                operation_key=UUID(data.get("operation_key") or str(uuid4())),
            )
        except (TypeError, ValueError) as exc:
            data["error"] = f"Review the solution content: {exc}"
            await manager.show(show_mode=ShowMode.EDIT)
            return
    data.update(
        operation_key=str(payload.operation_key),
        publish_payload=payload.model_dump(mode="json"),
        submitting=True,
        error="",
    )
    await manager.show(show_mode=ShowMode.EDIT)
    try:
        result: HomeworkSubmissionResult = await backend(manager).submit_homework(
            callback.from_user.id,
            callback.message.chat.id,
            UUID(assignment["id"]),
            payload,
        )
    except HomeworkOutcomeUnknown as exc:
        data.update(submitting=False, outcome_unknown=True, error=str(exc))
    except (BackendError, BackendUnavailable) as exc:
        data.update(
            submitting=False,
            outcome_unknown=False,
            operation_key=None,
            publish_payload=None,
            error=str(exc),
        )
    else:
        assignment["status"] = result.assignment_status
        assignment["completion_source"] = result.completion_source
        notice = (
            f"Solution saved as version {result.version}. "
            "This submission completed the assignment."
            if result.completion_source == "STUDENT_SUBMISSION"
            else f"Solution saved as version {result.version}."
        )
        data.update(
            assignment=assignment,
            attachments=[],
            text=None,
            pending_upload=None,
            uploading=False,
            upload_outcome_unknown=False,
            submitting=False,
            outcome_unknown=False,
            operation_key=None,
            publish_payload=None,
            error="",
            notice=notice,
        )
        await manager.switch_to(StudentHomeworkScreens.detail, show_mode=ShowMode.EDIT)
        return
    await manager.show(show_mode=ShowMode.EDIT)


async def cancel_submission(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Cancelling a solution requires a private chat")
    data = manager.dialog_data
    if data.get("outcome_unknown") or data.get("pending_upload"):
        return
    client = backend(manager)
    failed: list[str] = []
    retained: list[dict[str, Any]] = []
    for attachment in data.get("attachments", []):
        try:
            await client.delete_staged_homework_attachment(
                callback.from_user.id,
                callback.message.chat.id,
                UUID(attachment["id"]),
            )
        except BackendError as exc:
            if exc.status == 404:
                continue
            failed.append(str(exc))
            retained.append(attachment)
        except (BackendUnavailable, HomeworkOutcomeUnknown) as exc:
            failed.append(str(exc))
            retained.append(attachment)
    if failed:
        data.update(
            attachments=retained,
            error=f"Draft cleanup failed: {'; '.join(failed)} Retry Cancel.",
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    assignment = data.get("assignment")
    data.update(
        text=None,
        attachments=[],
        pending_upload=None,
        error="",
        notice="",
        operation_key=None,
        publish_payload=None,
    )
    if isinstance(assignment, dict):
        await manager.switch_to(StudentHomeworkScreens.detail, show_mode=ShowMode.EDIT)


async def retry_homework_list(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework list requires a private chat")
    data = manager.dialog_data
    data.update(error="", skip=0)
    try:
        page = await backend(manager).read_homework_assignments(
            callback.from_user.id,
            callback.message.chat.id,
            limit=ASSIGNMENT_PAGE_SIZE,
            status=data.get("status_filter"),
        )
    except (BackendError, BackendUnavailable) as exc:
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    _store_assignment_page(data, page, 0)
    await manager.switch_to(StudentHomeworkScreens.assignments, show_mode=ShowMode.EDIT)


async def close_homework(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.done(show_mode=ShowMode.EDIT)


def build_homework_student_dialog() -> Dialog:
    return Dialog(
        Window(
            Format(
                "Your homework — {status_filter}\n{page}\n"
                "Choose an assignment to open it.\n{notice}\n{error}"
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
                    id="previous",
                    on_click=previous_assignments,
                    when="has_previous",
                ),
                Button(
                    Const("Next"),
                    id="next",
                    on_click=next_assignments,
                    when="has_next",
                ),
            ),
            Button(Const("Close"), id="close", on_click=close_homework),
            state=StudentHomeworkScreens.assignments,
        ),
        Window(
            Format(
                "{assignment_text}\nStatus: {assignment_status}\n"
                "{completion_label}\nMaterials:\n{error}\n{notice}"
            ),
            Select(
                Format("{item[label]}"),
                id="material",
                item_id_getter=lambda item: item["id"],
                items="materials",
                on_click=download_material,
                when="has_materials",
            ),
            Row(
                Button(
                    Format("{retry_label}"),
                    id="submit",
                    on_click=begin_submission,
                    when="can_submit",
                ),
                Button(Const("Refresh"), id="refresh", on_click=refresh_assignment),
            ),
            Button(
                Const("Back to homework"),
                id="back",
                on_click=back_to_assignments,
            ),
            state=StudentHomeworkScreens.detail,
        ),
        Window(
            Format(
                "Solution for this assignment\n"
                "Send text and/or documents. Files are staged privately until Finish.\n"
                "Text: {text}\nFiles:\n{attachment_summary}\n"
                "{upload_status}\n{error}"
            ),
            TextInput("solution_text", on_success=set_solution_text),
            MessageInput(
                receive_solution_document,
                content_types=[ContentType.DOCUMENT],
            ),
            Column(
                Select(
                    Format("{item[label]}"),
                    id="remove_attachment",
                    item_id_getter=lambda item: item["id"],
                    items="attachments",
                    on_click=remove_solution_attachment,
                    when="has_attachments",
                ),
                Button(
                    Const("Clear text"),
                    id="clear_text",
                    on_click=clear_solution_text,
                    when="has_text and can_edit",
                ),
                Button(
                    Format("Retry upload: {pending_file_name}"),
                    id="retry_upload",
                    on_click=retry_solution_upload,
                    when="upload_unknown",
                ),
            ),
            Row(
                Button(
                    Const("Review"),
                    id="review",
                    on_click=review_submission,
                    when="can_review",
                ),
                Button(
                    Const("Cancel"),
                    id="cancel",
                    on_click=cancel_submission,
                    when="can_cancel",
                ),
            ),
            state=StudentHomeworkScreens.compose,
        ),
        Window(
            Format(
                "Review solution for {assignment_text}\n"
                "Text: {text}\nFiles:\n{attachment_summary}\n{error}"
            ),
            Button(
                Format("{submit_label}"),
                id="finish",
                on_click=finish_submission,
                when="can_finish",
            ),
            Button(
                Const("Back"),
                id="back",
                on_click=return_to_compose,
                when="can_edit",
            ),
            Button(
                Const("Cancel"),
                id="cancel",
                on_click=cancel_submission,
                when="can_cancel",
            ),
            state=StudentHomeworkScreens.review,
        ),
        Window(
            Format("{error}\nReturn to the homework list and retry."),
            Button(
                Const("Retry"),
                id="retry",
                on_click=retry_homework_list,
            ),
            Button(Const("Close"), id="close", on_click=close_homework),
            state=StudentHomeworkScreens.unavailable,
        ),
        getter=student_homework_data,
        on_start=student_homework_started,
    )

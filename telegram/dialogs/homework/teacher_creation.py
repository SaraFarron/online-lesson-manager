from collections.abc import AsyncIterable
from datetime import date, datetime, timedelta
from html import escape
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

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
    BackendClient,
    BackendError,
    BackendUnavailable,
    EventManagementPage,
    HomeworkAssignmentPayload,
    HomeworkAttachmentTooLarge,
    HomeworkLessonOccurrence,
    HomeworkOutcomeUnknown,
    ManagementResource,
    ScheduleResponse,
    SessionView,
    StagedHomeworkAttachment,
    StudentPickerPage,
)
from telegram.dialogs.homework.text import (
    change_homework_text_page,
    homework_text_page,
)
from telegram.shared.access import backend
from telegram.shared.feedback import feedback_block
from telegram.shared.inputs import format_compact_schedule_date

STUDENT_PAGE_SIZE = 20
LESSON_PAGE_SIZE = 10


class HomeworkScreens(StatesGroup):
    students = State()
    compose = State()
    lessons = State()
    review = State()
    unavailable = State()


async def open_homework_creation(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework creation requires a private chat")
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role != "TEACHER":
        raise ValueError("Only teachers can create homework")

    data: dict[str, Any] = {
        "view": view.model_dump(mode="json"),
        "viewer_timezone": view.account.timezone,
        "picker_items": [],
        "picker_total": 0,
        "picker_skip": 0,
        "student_id": None,
        "student_label": "",
        "text": None,
        "attachments": [],
        "lesson_occurrence": None,
        "lesson_options": [],
        "lesson_skip": 0,
        "lesson_week": None,
        "lesson_error": "",
        "pending_upload": None,
        "uploading": False,
        "upload_outcome_unknown": False,
        "operation_key": None,
        "submitting": False,
        "outcome_unknown": False,
        "error": "",
        "warning": "",
        "text_page": 0,
    }
    try:
        page = await backend(manager).read_student_picker(
            callback.from_user.id,
            callback.message.chat.id,
            limit=STUDENT_PAGE_SIZE,
        )
        _store_picker_page(data, page, 0)
        state = HomeworkScreens.students
    except (BackendError, BackendUnavailable) as exc:
        data["error"] = str(exc)
        state = HomeworkScreens.unavailable

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


async def homework_started(start_data: Any, manager: DialogManager) -> None:
    manager.dialog_data.clear()
    manager.dialog_data.update(start_data)


async def previous_text_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await change_homework_text_page(manager, "text_page", -1)


async def next_text_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await change_homework_text_page(manager, "text_page", 1)


async def homework_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    students = [
        (escape(item["display_label"]), str(item["id"]))
        for item in data.get("picker_items", [])
    ]
    skip = int(data.get("picker_skip", 0))
    total = int(data.get("picker_total", 0))
    page_count = max(1, (total + STUDENT_PAGE_SIZE - 1) // STUDENT_PAGE_SIZE)
    page_number = skip // STUDENT_PAGE_SIZE + 1
    attachments = data.get("attachments", [])
    attachment_lines = [
        f"{escape(str(item['file_name']))} ({int(item['byte_size'])} bytes)"
        for item in attachments
    ]
    attachment_choices = [
        {
            "id": str(item["id"]),
            "label": f"Remove {escape(str(item['file_name']))}",
        }
        for item in attachments
    ]
    text = str(data.get("text") or "")
    text_page = homework_text_page(text, int(data.get("text_page", 0)), label="Text")
    pending_upload = data.get("pending_upload")
    active = bool(data.get("submitting") or data.get("uploading"))
    lesson_occurrence = data.get("lesson_occurrence")
    lesson_options = data.get("lesson_options", [])
    lesson_skip = int(data.get("lesson_skip", 0))
    lesson_option_page = lesson_options[lesson_skip : lesson_skip + LESSON_PAGE_SIZE]
    lesson_label = "(not linked)"
    if isinstance(lesson_occurrence, dict):
        lesson_label = escape(str(lesson_occurrence["label"]))
    upload_status = ""
    if data.get("uploading"):
        pending_file_name = (
            str(pending_upload.get("file_name", "file"))
            if isinstance(pending_upload, dict)
            else "file"
        )
        upload_status = f"Uploading {escape(pending_file_name)} to private storage."
    elif data.get("upload_outcome_unknown"):
        upload_status = "Upload result unknown; retry the same file to reconcile it."
    errors = [
        str(value)
        for value in (data.get("error", ""), data.get("lesson_error", ""))
        if value
    ]
    return {
        "students": students,
        "picker_page": f"Page {page_number} of {page_count} ({total} students)",
        "has_previous_students": skip > 0,
        "has_next_students": skip + len(students) < total,
        "student_label": escape(str(data.get("student_label", ""))),
        "text": text_page.content,
        "text_heading": text_page.heading,
        "attachments": attachment_choices,
        "attachment_summary": (
            "\n".join(attachment_lines) if attachment_lines else "(no files)"
        ),
        "feedback": feedback_block(
            error="\n".join(errors) or None,
            warning=str(data.get("warning", "")) or None,
        ),
        "has_previous_text_page": text_page.has_previous,
        "has_next_text_page": text_page.has_next,
        "upload_status": upload_status,
        "can_review": bool(text.strip() or attachments)
        and not pending_upload
        and not active
        and not data.get("outcome_unknown"),
        "can_publish": bool(text.strip() or attachments)
        and not pending_upload
        and not active,
        "can_cancel": not pending_upload
        and not active
        and not data.get("outcome_unknown"),
        "has_text": bool(text),
        "has_attachments": bool(attachments),
        "upload_unknown": bool(data.get("upload_outcome_unknown")),
        "pending_file_name": escape(
            str(pending_upload.get("file_name", "file")) if pending_upload else "file"
        ),
        "can_edit_review": not bool(data.get("outcome_unknown")) and not active,
        "lesson_label": lesson_label,
        "lesson_options": lesson_option_page,
        "lesson_week_label": escape(str(data.get("lesson_week_label", ""))),
        "lesson_timezone": escape(str(data.get("viewer_timezone", "UTC"))),
        "lesson_error": escape(str(data.get("lesson_error", ""))),
        "lesson_page": (
            ""
            if lesson_options or data.get("lesson_error")
            else "No upcoming lessons for this student in this week."
        ),
        "has_previous_lesson_page": lesson_skip > 0,
        "has_next_lesson_page": lesson_skip + LESSON_PAGE_SIZE < len(lesson_options),
        "has_lesson": bool(lesson_occurrence),
        "can_edit_lesson": not bool(data.get("outcome_unknown")) and not active,
        "lesson_picker_has_options": bool(lesson_options),
        "submit_label": (
            "Retry publish (same operation)"
            if data.get("outcome_unknown")
            else "Publish assignment"
        ),
    }


async def select_student(
    _callback: CallbackQuery, _widget: Select[str], manager: DialogManager, item: str
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
        student_id=item,
        student_label=selected["display_label"],
        text=None,
        text_page=0,
        attachments=[],
        lesson_occurrence=None,
        pending_upload=None,
        operation_key=None,
        error="",
        warning="",
    )
    await manager.switch_to(HomeworkScreens.compose, show_mode=ShowMode.EDIT)


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
    except (BackendError, BackendUnavailable) as exc:
        data["error"] = str(exc)
    else:
        _store_picker_page(data, page, skip)
        data["error"] = ""
    await manager.show(show_mode=ShowMode.EDIT)


async def open_lesson_picker(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Lesson selection requires a private chat")
    if manager.dialog_data.get("outcome_unknown"):
        return
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role != "TEACHER":
        raise ValueError("Only teachers can link lessons to homework")
    if not manager.dialog_data.get("student_id"):
        raise ValueError("Select a student before linking a lesson")
    requested_date = (
        date.fromisoformat(manager.dialog_data["lesson_week"])
        if manager.dialog_data.get("lesson_week")
        else datetime.now(ZoneInfo(view.account.timezone)).date()
    )
    await _load_lesson_options(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        requested_date,
    )
    await manager.switch_to(HomeworkScreens.lessons, show_mode=ShowMode.EDIT)


async def _read_student_lesson_ids(
    client: BackendClient,
    sender_id: int,
    chat_id: int,
    resource: ManagementResource,
    student_id: UUID,
) -> set[UUID]:
    ids: set[UUID] = set()
    skip = 0
    while True:
        page: EventManagementPage = await client.read_management_page(
            sender_id,
            chat_id,
            resource,
            skip=skip,
            limit=100,
            subject_id=student_id,
        )
        ids.update(item.id for item in page.items)
        skip += len(page.items)
        if skip >= page.total or not page.items:
            return ids


async def _load_lesson_options(
    sender_id: int,
    chat_id: int,
    manager: DialogManager,
    requested_date: date,
) -> None:
    data = manager.dialog_data
    data.update(lesson_options=[], lesson_skip=0, lesson_error="")
    view = SessionView.model_validate(data["view"])
    viewer_timezone = ZoneInfo(view.account.timezone)
    reference_date = datetime.now(viewer_timezone).date()
    week_start = requested_date - timedelta(days=requested_date.weekday())
    week_end = week_start + timedelta(days=6)
    fallback_week_label = (
        f"{format_compact_schedule_date(week_start, reference_date=reference_date)} "
        f"– {format_compact_schedule_date(week_end, reference_date=reference_date)}"
    )
    try:
        client = backend(manager)
        student_id = UUID(data["student_id"])
        one_off_ids = await _read_student_lesson_ids(
            client, sender_id, chat_id, "lessons", student_id
        )
        recurrent_ids = await _read_student_lesson_ids(
            client, sender_id, chat_id, "recurrent-lessons", student_id
        )
        schedule: ScheduleResponse = await client.read_schedule_week(
            sender_id, chat_id, requested_date
        )
    except (BackendError, BackendUnavailable) as exc:
        data["lesson_error"] = str(exc)
        data["lesson_week_label"] = fallback_week_label
        data["lesson_week"] = requested_date.isoformat()
        return

    source_ids = {"event": one_off_ids, "recurrent_event": recurrent_ids}
    options: list[dict[str, str]] = []
    for day in schedule.days:
        for item in day.items:
            if (
                item.status != "upcoming"
                or item.event_type != "LESSON"
                or item.source_id not in source_ids.get(item.source_kind, set())
            ):
                continue
            local_start = item.start.astimezone(viewer_timezone)
            kind = "EVENT" if item.source_kind == "event" else "RECURRENT_EVENT"
            options.append(
                {
                    "kind": kind,
                    "source_id": str(item.source_id),
                    "starts_at": item.start.isoformat(),
                    "label": escape(
                        f"{format_compact_schedule_date(local_start.date(), reference_date=reference_date)} "
                        f"{local_start.hour}:{local_start.minute:02d} "
                        f"({kind.replace('_', ' ').lower()})"
                    ),
                }
            )
    options.sort(key=lambda option: option["starts_at"])
    data.update(
        lesson_options=[
            {**option, "index": str(index)} for index, option in enumerate(options)
        ],
        lesson_skip=0,
        lesson_week=requested_date.isoformat(),
        lesson_week_label=(
            f"{format_compact_schedule_date(schedule.days[0].date, reference_date=reference_date)} "
            f"– {format_compact_schedule_date(schedule.days[-1].date, reference_date=reference_date)}"
        ),
        lesson_error="",
    )


async def previous_lesson_week(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _navigate_lesson_week(callback, manager, -7)


async def next_lesson_week(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _navigate_lesson_week(callback, manager, 7)


async def _navigate_lesson_week(
    callback: CallbackQuery, manager: DialogManager, offset: int
) -> None:
    if callback.message is None:
        raise ValueError("Lesson selection requires a private chat")
    week = date.fromisoformat(manager.dialog_data["lesson_week"])
    await _load_lesson_options(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        week + timedelta(days=offset),
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def select_lesson_occurrence(
    _callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    selected = next(
        (
            option
            for option in manager.dialog_data.get("lesson_options", [])
            if option["index"] == item
        ),
        None,
    )
    if selected is None:
        manager.dialog_data["lesson_error"] = (
            "Select an occurrence from the current lesson list."
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    manager.dialog_data.update(
        lesson_occurrence={
            "kind": selected["kind"],
            "source_id": selected["source_id"],
            "starts_at": selected["starts_at"],
            "label": selected["label"],
        },
        operation_key=None,
        error="",
    )
    await manager.switch_to(HomeworkScreens.compose, show_mode=ShowMode.EDIT)


async def clear_lesson_occurrence(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        return
    manager.dialog_data.update(
        lesson_occurrence=None, operation_key=None, lesson_error="", error=""
    )
    await manager.switch_to(HomeworkScreens.compose, show_mode=ShowMode.EDIT)


async def return_from_lesson_picker(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.switch_to(HomeworkScreens.compose, show_mode=ShowMode.EDIT)


async def previous_lesson_options(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["lesson_skip"] = max(
        0, int(manager.dialog_data.get("lesson_skip", 0)) - LESSON_PAGE_SIZE
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def next_lesson_options(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["lesson_skip"] = (
        int(manager.dialog_data.get("lesson_skip", 0)) + LESSON_PAGE_SIZE
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def set_assignment_text(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    text = value.strip()
    if not text:
        manager.dialog_data["error"] = "Assignment text cannot be blank."
    else:
        manager.dialog_data.update(
            text=text, text_page=0, error="", warning="", operation_key=None
        )
    await manager.show(show_mode=ShowMode.EDIT)


async def clear_assignment_text(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        return
    manager.dialog_data.update(
        text=None, text_page=0, error="", warning="", operation_key=None
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def receive_document(
    message: Message, _widget: MessageInput, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        manager.dialog_data["error"] = (
            "Retry the pending publish with the same operation before editing it."
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    if manager.dialog_data.get("pending_upload"):
        manager.dialog_data["error"] = (
            "Retry the pending upload before sending another file."
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    if message.from_user is None or message.document is None:
        raise ValueError("Homework upload requires a Telegram document")

    attachments = manager.dialog_data.get("attachments", [])
    if len(attachments) >= MAX_HOMEWORK_ATTACHMENTS:
        manager.dialog_data["error"] = (
            f"An assignment can contain at most {MAX_HOMEWORK_ATTACHMENTS} files."
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
            "The file or assignment exceeds the 20 MiB per-file or 50 MiB total limit."
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return

    manager.dialog_data.update(
        pending_upload={
            "telegram_file_id": message.document.file_id,
            "file_name": message.document.file_name or "attachment",
            "advertised_size": advertised_size,
            "operation_key": str(uuid4()),
        },
        upload_outcome_unknown=False,
        error="",
        warning="",
    )
    await _upload_pending(message.from_user.id, message.chat.id, manager)


async def retry_pending_upload(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Homework upload requires a private chat")
    await _upload_pending(callback.from_user.id, callback.message.chat.id, manager)


async def _upload_pending(sender_id: int, chat_id: int, manager: DialogManager) -> None:
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
                upload_outcome_unknown=False,
                uploading=False,
                error="Telegram could not provide a download path. Please send the file again.",
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
            error="",
            warning=str(exc),
        )
    except (BackendError, BackendUnavailable, HomeworkAttachmentTooLarge) as exc:
        data.update(
            pending_upload=None,
            uploading=False,
            upload_outcome_unknown=False,
            error=str(exc),
            warning="",
        )
    except TelegramAPIError:
        data.update(
            pending_upload=None,
            uploading=False,
            upload_outcome_unknown=False,
            error="Telegram could not retrieve this file. Please send it again.",
            warning="",
        )
    else:
        attachments = data.get("attachments", [])
        attachments.append(staged.model_dump(mode="json"))
        data.update(
            attachments=attachments,
            pending_upload=None,
            uploading=False,
            upload_outcome_unknown=False,
            error="",
            warning="",
            operation_key=None,
        )
        total_size = sum(int(item["byte_size"]) for item in attachments)
        if total_size > MAX_HOMEWORK_ATTACHMENT_TOTAL_BYTES:
            data["error"] = (
                "The uploaded file puts this assignment over the 50 MiB total limit. "
                "Remove a file before publishing."
            )
    await manager.show(show_mode=ShowMode.EDIT)


async def remove_attachment(
    callback: CallbackQuery,
    _widget: Select[str],
    manager: DialogManager,
    item: str,
) -> None:
    if callback.message is None:
        raise ValueError("Removing a homework attachment requires a private chat")
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
    except HomeworkOutcomeUnknown as exc:
        manager.dialog_data.update(error="", warning=str(exc))
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        manager.dialog_data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    attachments.remove(selected)
    manager.dialog_data.update(
        attachments=attachments, error="", warning="", operation_key=None
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def review_assignment(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    if data.get("outcome_unknown") or data.get("pending_upload"):
        return
    text = str(data.get("text") or "").strip()
    attachments = data.get("attachments", [])
    if not text and not attachments:
        data["error"] = "Add assignment text or at least one file before reviewing."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    total_size = sum(int(item["byte_size"]) for item in attachments)
    if total_size > MAX_HOMEWORK_ATTACHMENT_TOTAL_BYTES:
        data["error"] = "Remove files until the assignment is within the 50 MiB limit."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data["error"] = ""
    await manager.switch_to(HomeworkScreens.review, show_mode=ShowMode.EDIT)


async def return_to_compose(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        return
    await manager.switch_to(HomeworkScreens.compose, show_mode=ShowMode.EDIT)


async def publish_assignment(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Publishing homework requires a private chat")
    data = manager.dialog_data
    if data.get("submitting"):
        return
    if data.get("pending_upload"):
        data["error"] = "Finish or retry the pending file upload before publishing."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    if not data.get("outcome_unknown"):
        text = str(data.get("text") or "").strip() or None
        attachment_ids = [UUID(item["id"]) for item in data.get("attachments", [])]
        try:
            raw_lesson = data.get("lesson_occurrence")
            lesson_occurrence = (
                HomeworkLessonOccurrence(
                    kind=raw_lesson["kind"],
                    source_id=UUID(raw_lesson["source_id"]),
                    starts_at=raw_lesson["starts_at"],
                )
                if raw_lesson
                else None
            )
            payload = HomeworkAssignmentPayload(
                student_id=UUID(data["student_id"]),
                text=text,
                attachment_ids=attachment_ids,
                lesson_occurrence=lesson_occurrence,
            )
        except (KeyError, TypeError, ValueError) as exc:
            data["error"] = f"Review the assignment content: {exc}"
            await manager.show(show_mode=ShowMode.EDIT)
            return
    else:
        payload_data = data.get("publish_payload")
        if not isinstance(payload_data, dict):
            data["error"] = (
                "The pending publish cannot be reconciled. Cancel and retry."
            )
            await manager.show(show_mode=ShowMode.EDIT)
            return
        try:
            payload = HomeworkAssignmentPayload.model_validate(payload_data)
        except ValueError as exc:
            data["error"] = f"The pending assignment is invalid: {exc}"
            await manager.show(show_mode=ShowMode.EDIT)
            return

    operation_key = data.get("operation_key") or str(uuid4())
    data.update(
        operation_key=operation_key,
        publish_payload=payload.model_dump(mode="json"),
        submitting=True,
        error="",
        warning="",
    )
    await manager.show(show_mode=ShowMode.EDIT)
    try:
        await backend(manager).create_homework_assignment(
            callback.from_user.id,
            callback.message.chat.id,
            payload,
            UUID(operation_key),
        )
    except HomeworkOutcomeUnknown as exc:
        data.update(
            submitting=False,
            outcome_unknown=True,
            error="",
            warning=str(exc),
        )
    except (BackendError, BackendUnavailable) as exc:
        data.update(submitting=False, error=str(exc), warning="", operation_key=None)
    else:
        await manager.done(
            result={"homework_notice": "Homework assignment published."},
            show_mode=ShowMode.EDIT,
        )
        return
    await manager.show(show_mode=ShowMode.EDIT)


async def cancel_assignment(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Cancelling homework requires a private chat")
    data = manager.dialog_data
    if data.get("outcome_unknown") or data.get("pending_upload"):
        return
    client = backend(manager)
    failed: list[str] = []
    unknown: list[str] = []
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
        except HomeworkOutcomeUnknown as exc:
            unknown.append(str(exc))
            retained.append(attachment)
        except BackendUnavailable as exc:
            failed.append(str(exc))
            retained.append(attachment)
    if failed or unknown:
        data.update(
            attachments=retained,
            error=(
                f"Draft cleanup failed: {'; '.join(failed)} Retry Cancel."
                if failed
                else ""
            ),
            warning=(
                f"Draft cleanup outcome is unknown: {'; '.join(unknown)} "
                "Retry Cancel to reconcile retained files."
                if unknown
                else ""
            ),
        )
        await manager.show(show_mode=ShowMode.EDIT)
        return
    await manager.done(show_mode=ShowMode.EDIT)


async def back_to_home(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.done(show_mode=ShowMode.EDIT)


def build_homework_teacher_dialog() -> Dialog:
    return Dialog(
        Window(
            Format("{feedback}Choose a student for the assignment.\n{picker_page}"),
            Select(
                Format("{item[0]}"),
                id="student",
                item_id_getter=lambda item: item[1],
                items="students",
                on_click=select_student,
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
            Button(Const("Cancel"), id="cancel", on_click=back_to_home),
            state=HomeworkScreens.students,
        ),
        Window(
            Format(
                "{feedback}Assignment for {student_label}\n"
                "Send a text message for the assignment text, or send documents "
                "to add files. You can use either or both.\n"
                "{text_heading}{text}\nFiles:\n{attachment_summary}\n"
                "Lesson: {lesson_label}\n"
                "{upload_status}"
            ),
            TextInput("assignment_text", on_success=set_assignment_text),
            MessageInput(
                receive_document,
                content_types=[ContentType.DOCUMENT],
            ),
            Column(
                Row(
                    Button(
                        Const("Previous text"),
                        id="previous_text",
                        on_click=previous_text_page,
                        when="has_previous_text_page",
                    ),
                    Button(
                        Const("Next text"),
                        id="next_text",
                        on_click=next_text_page,
                        when="has_next_text_page",
                    ),
                ),
                Select(
                    Format("{item[label]}"),
                    id="remove_attachment",
                    item_id_getter=lambda item: item["id"],
                    items="attachments",
                    on_click=remove_attachment,
                    when="has_attachments",
                ),
                Button(
                    Const("Clear text"),
                    id="clear_text",
                    on_click=clear_assignment_text,
                    when="has_text and can_edit_lesson",
                ),
                Button(
                    Const("Link or change lesson"),
                    id="link_lesson",
                    on_click=open_lesson_picker,
                    when="can_edit_lesson",
                ),
                Button(
                    Const("Remove lesson link"),
                    id="unlink_lesson",
                    on_click=clear_lesson_occurrence,
                    when="has_lesson and can_edit_lesson",
                ),
                Button(
                    Format("Retry upload: {pending_file_name}"),
                    id="retry_upload",
                    on_click=retry_pending_upload,
                    when="upload_unknown",
                ),
            ),
            Row(
                Button(
                    Const("Review"),
                    id="review",
                    on_click=review_assignment,
                    when="can_review",
                ),
                Button(
                    Const("Cancel"),
                    id="cancel",
                    on_click=cancel_assignment,
                    when="can_cancel",
                ),
            ),
            state=HomeworkScreens.compose,
        ),
        Window(
            Format(
                "{feedback}Select an upcoming lesson for {student_label}.\n"
                "Week: {lesson_week_label}\n{lesson_page}\n"
                "Timezone: {lesson_timezone}"
            ),
            Select(
                Format("{item[label]}"),
                id="lesson_occurrence",
                item_id_getter=lambda item: item["index"],
                items="lesson_options",
                on_click=select_lesson_occurrence,
                when="lesson_picker_has_options",
            ),
            Row(
                Button(
                    Const("Earlier week"),
                    id="previous_lesson_week",
                    on_click=previous_lesson_week,
                ),
                Button(
                    Const("Later week"),
                    id="next_lesson_week",
                    on_click=next_lesson_week,
                ),
            ),
            Row(
                Button(
                    Const("Previous options"),
                    id="previous_lesson_options",
                    on_click=previous_lesson_options,
                    when="has_previous_lesson_page",
                ),
                Button(
                    Const("Next options"),
                    id="next_lesson_options",
                    on_click=next_lesson_options,
                    when="has_next_lesson_page",
                ),
            ),
            Button(
                Const("No lesson association"),
                id="clear_lesson",
                on_click=clear_lesson_occurrence,
                when="can_edit_lesson",
            ),
            Button(
                Const("Back"),
                id="back",
                on_click=return_from_lesson_picker,
            ),
            state=HomeworkScreens.lessons,
        ),
        Window(
            Format(
                "{feedback}Review assignment for {student_label}\n"
                "{text_heading}{text}\nFiles:\n{attachment_summary}\n"
                "Lesson: {lesson_label}"
            ),
            Row(
                Button(
                    Const("Previous text"),
                    id="previous_text",
                    on_click=previous_text_page,
                    when="has_previous_text_page",
                ),
                Button(
                    Const("Next text"),
                    id="next_text",
                    on_click=next_text_page,
                    when="has_next_text_page",
                ),
            ),
            Button(
                Format("{submit_label}"),
                id="publish",
                on_click=publish_assignment,
                when="can_publish",
            ),
            Button(
                Const("Back"),
                id="back",
                on_click=return_to_compose,
                when="can_edit_review",
            ),
            Button(
                Const("Cancel"),
                id="cancel",
                on_click=cancel_assignment,
                when="can_cancel",
            ),
            state=HomeworkScreens.review,
        ),
        Window(
            Format("{feedback}Return home and retry when the backend is available."),
            Button(Const("Back to home"), id="home", on_click=back_to_home),
            state=HomeworkScreens.unavailable,
        ),
        getter=homework_data,
        on_start=homework_started,
    )

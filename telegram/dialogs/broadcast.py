from html import escape
from typing import Any
from uuid import UUID, uuid4

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, ShowMode, StartMode, Window
from aiogram_dialog.widgets.input import ManagedTextInput, TextInput
from aiogram_dialog.widgets.kbd import Button, Row
from aiogram_dialog.widgets.text import Const, Format
from pydantic import ValidationError

from telegram.api import (
    BackendError,
    BackendUnavailable,
    BroadcastOutcomeUnknown,
    BroadcastPreviewStale,
    SessionView,
    TeacherBroadcastPreview,
    TeacherBroadcastPreviewRequest,
    TeacherBroadcastPublication,
    TeacherBroadcastPublishRequest,
    TeacherBroadcastResults,
)
from telegram.dialogs.homework.text import homework_text_page
from telegram.shared.access import backend
from telegram.shared.feedback import feedback_block

RESULT_PAGE_SIZE = 10
_STATUS_LABELS = {
    "pending": "Pending",
    "retrying": "Retrying",
    "sent": "Sent",
    "suppressed": "Suppressed",
    "failed": "Failed",
    "unknown": "Unknown",
}


class BroadcastScreens(StatesGroup):
    compose = State()
    review = State()
    outcome_unknown = State()
    results = State()


async def open_teacher_broadcast(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Broadcast composition requires a private chat")
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role != "TEACHER":
        raise ValueError("Only teachers can publish broadcasts")
    await manager.start(
        BroadcastScreens.compose,
        data={
            "view": view.model_dump(mode="json"),
            "text": "",
            "text_page": 0,
            "preview_token": None,
            "preview": None,
            "preview_text": None,
            "operation_key": None,
            "publish_payload": None,
            "publication": None,
            "results": None,
            "results_skip": 0,
            "outcome_unknown": False,
            "outcome_missing": False,
            "submitting": False,
            "loading_results": False,
            "error": "",
            "warning": "",
        },
        mode=StartMode.NORMAL,
        show_mode=ShowMode.EDIT,
    )


async def broadcast_started(start_data: Any, manager: DialogManager) -> None:
    manager.dialog_data.clear()
    manager.dialog_data.update(start_data)


def _validate_broadcast_text(value: str) -> str:
    return TeacherBroadcastPreviewRequest(text=value).text


async def set_broadcast_text(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    value: str,
) -> None:
    manager.dialog_data.update(
        text=value,
        text_page=0,
        preview_token=None,
        preview=None,
        preview_text=None,
        operation_key=None,
        publish_payload=None,
        error="",
        warning="",
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def broadcast_text_error(
    _message: Message,
    _widget: ManagedTextInput[str],
    manager: DialogManager,
    error: ValueError,
) -> None:
    manager.dialog_data.update(
        error=str(error),
        preview_token=None,
        preview=None,
        preview_text=None,
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def previous_text_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["text_page"] = max(
        0, int(manager.dialog_data.get("text_page", 0)) - 1
    )
    await manager.show(show_mode=ShowMode.EDIT)


async def next_text_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    manager.dialog_data["text_page"] = int(manager.dialog_data.get("text_page", 0)) + 1
    await manager.show(show_mode=ShowMode.EDIT)


async def preview_broadcast(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Broadcast preview requires a private chat")
    data = manager.dialog_data
    try:
        request = TeacherBroadcastPreviewRequest(text=str(data.get("text", "")))
        preview = await backend(manager).preview_teacher_broadcast(
            callback.from_user.id,
            callback.message.chat.id,
            request.text,
        )
    except (BackendError, BackendUnavailable, ValidationError, ValueError) as exc:
        data["error"] = str(exc)
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data.update(
        preview_token=preview.preview_token,
        preview=preview.model_dump(mode="json"),
        preview_text=request.text,
        text_page=0,
        error="",
        warning="",
    )
    await manager.switch_to(BroadcastScreens.review, show_mode=ShowMode.EDIT)


async def back_to_compose(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        return
    await manager.switch_to(BroadcastScreens.compose, show_mode=ShowMode.EDIT)


async def cancel_broadcast(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if manager.dialog_data.get("outcome_unknown"):
        return
    await manager.done(show_mode=ShowMode.EDIT)


async def confirm_broadcast(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    token = data.get("preview_token")
    preview_text = data.get("preview_text")
    text = data.get("text")
    if not isinstance(token, str) or not isinstance(text, str) or text != preview_text:
        data.update(
            preview_token=None,
            preview=None,
            preview_text=None,
            error="Review the message and preview it again before sending.",
        )
        await manager.switch_to(BroadcastScreens.compose, show_mode=ShowMode.EDIT)
        return
    try:
        request = TeacherBroadcastPublishRequest(text=text, preview_token=token)
    except ValidationError as exc:
        data["error"] = str(exc)
        await manager.switch_to(BroadcastScreens.compose, show_mode=ShowMode.EDIT)
        return
    await _publish_operation(callback, manager, request, uuid4())


async def retry_pending_broadcast(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    raw_payload = data.get("publish_payload")
    raw_key = data.get("operation_key")
    if not isinstance(raw_payload, dict) or not isinstance(raw_key, str):
        data["error"] = "The pending operation cannot be recovered. Contact support."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    try:
        request = TeacherBroadcastPublishRequest.model_validate(raw_payload)
        operation_key = UUID(raw_key)
    except (ValidationError, ValueError) as exc:
        data["error"] = f"The pending operation is invalid: {exc}"
        await manager.show(show_mode=ShowMode.EDIT)
        return
    await _publish_operation(callback, manager, request, operation_key)


async def _publish_operation(
    callback: CallbackQuery,
    manager: DialogManager,
    request: TeacherBroadcastPublishRequest,
    operation_key: UUID,
) -> None:
    if callback.message is None:
        raise ValueError("Broadcast publishing requires a private chat")
    data = manager.dialog_data
    if data.get("submitting"):
        return
    data.update(
        operation_key=str(operation_key),
        publish_payload=request.model_dump(mode="json"),
        submitting=True,
        outcome_unknown=False,
        outcome_missing=False,
        error="",
        warning="",
    )
    await manager.show(show_mode=ShowMode.EDIT)
    try:
        publication = await backend(manager).publish_teacher_broadcast(
            callback.from_user.id,
            callback.message.chat.id,
            request,
            operation_key,
        )
    except BroadcastPreviewStale as exc:
        data.update(
            preview_token=None,
            preview=None,
            operation_key=None,
            publish_payload=None,
            submitting=False,
            error=str(exc),
            warning="",
        )
        await manager.switch_to(BroadcastScreens.compose, show_mode=ShowMode.EDIT)
        return
    except BroadcastOutcomeUnknown as exc:
        data.update(
            submitting=False,
            outcome_unknown=True,
            outcome_missing=False,
            operation_key=str(exc.operation_key),
            publish_payload=exc.request.model_dump(mode="json"),
            error="",
            warning=str(exc),
        )
        await manager.switch_to(
            BroadcastScreens.outcome_unknown, show_mode=ShowMode.EDIT
        )
        return
    except BackendError as exc:
        data.update(
            submitting=False,
            operation_key=None,
            publish_payload=None,
            error=str(exc),
            warning="",
        )
        await manager.switch_to(BroadcastScreens.review, show_mode=ShowMode.EDIT)
        return
    except BackendUnavailable as exc:
        data.update(
            submitting=False,
            outcome_unknown=True,
            outcome_missing=False,
            error="",
            warning=str(exc),
        )
        await manager.switch_to(
            BroadcastScreens.outcome_unknown, show_mode=ShowMode.EDIT
        )
        return

    data.update(
        submitting=False,
        outcome_unknown=False,
        outcome_missing=False,
        publication=publication.model_dump(mode="json"),
        results=None,
        results_skip=0,
        error="",
        warning="",
    )
    await manager.switch_to(BroadcastScreens.results, show_mode=ShowMode.EDIT)
    await _load_results(callback.from_user.id, callback.message.chat.id, manager, 0)


async def reconcile_pending_broadcast(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Broadcast reconciliation requires a private chat")
    data = manager.dialog_data
    raw_key = data.get("operation_key")
    if not isinstance(raw_key, str):
        data["error"] = "The pending operation key is missing."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    try:
        operation_key = UUID(raw_key)
        publication = await backend(manager).reconcile_teacher_broadcast(
            callback.from_user.id,
            callback.message.chat.id,
            operation_key,
        )
    except BackendError as exc:
        if exc.status == 404:
            data.update(
                outcome_missing=True,
                error="",
                warning=(
                    "No publication is recorded yet. Retry only this same operation."
                ),
            )
        else:
            data.update(error="", warning=str(exc))
        await manager.show(show_mode=ShowMode.EDIT)
        return
    except (BackendUnavailable, ValueError) as exc:
        data.update(error="", warning=str(exc))
        await manager.show(show_mode=ShowMode.EDIT)
        return
    await _show_publication_results(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        publication,
    )


async def _show_publication_results(
    sender_id: int,
    chat_id: int,
    manager: DialogManager,
    publication: TeacherBroadcastPublication,
) -> None:
    manager.dialog_data.update(
        publication=publication.model_dump(mode="json"),
        outcome_unknown=False,
        outcome_missing=False,
        submitting=False,
        results=None,
        results_skip=0,
        error="",
        warning="",
    )
    await manager.switch_to(BroadcastScreens.results, show_mode=ShowMode.EDIT)
    await _load_results(sender_id, chat_id, manager, 0)


async def refresh_broadcast_results(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Broadcast results require a private chat")
    await _load_results(
        callback.from_user.id,
        callback.message.chat.id,
        manager,
        int(manager.dialog_data.get("results_skip", 0)),
    )


async def previous_result_page(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Broadcast results require a private chat")
    skip = max(0, int(manager.dialog_data.get("results_skip", 0)) - RESULT_PAGE_SIZE)
    await _load_results(callback.from_user.id, callback.message.chat.id, manager, skip)


async def next_result_page(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    if callback.message is None:
        raise ValueError("Broadcast results require a private chat")
    skip = int(manager.dialog_data.get("results_skip", 0)) + RESULT_PAGE_SIZE
    await _load_results(callback.from_user.id, callback.message.chat.id, manager, skip)


async def _load_results(
    sender_id: int,
    chat_id: int,
    manager: DialogManager,
    skip: int,
) -> None:
    data = manager.dialog_data
    raw_key = data.get("operation_key")
    if not isinstance(raw_key, str):
        data["error"] = "The broadcast operation key is missing."
        await manager.show(show_mode=ShowMode.EDIT)
        return
    data.update(results_skip=skip, loading_results=True, error="", warning="")
    await manager.show(show_mode=ShowMode.EDIT)
    try:
        results = await backend(manager).read_teacher_broadcast_results(
            sender_id,
            chat_id,
            UUID(raw_key),
            skip=skip,
            limit=RESULT_PAGE_SIZE,
        )
    except (BackendError, BackendUnavailable, ValueError) as exc:
        data.update(loading_results=False, error=str(exc))
    else:
        data.update(
            results=results.model_dump(mode="json"),
            results_skip=results.skip,
            loading_results=False,
            error="",
        )
    await manager.show(show_mode=ShowMode.EDIT)


async def back_to_home(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.done(show_mode=ShowMode.EDIT)


async def broadcast_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    data = dialog_manager.dialog_data
    text_page = homework_text_page(
        str(data.get("text") or ""),
        int(data.get("text_page", 0)),
        label="Message",
    )
    raw_preview = data.get("preview")
    preview = (
        TeacherBroadcastPreview.model_validate(raw_preview)
        if isinstance(raw_preview, dict)
        else None
    )
    raw_results = data.get("results")
    results = (
        TeacherBroadcastResults.model_validate(raw_results)
        if isinstance(raw_results, dict)
        else None
    )
    recipient_lines = []
    if results is not None:
        recipient_lines = [
            f"{escape(item.student_name or 'Student')}: {_STATUS_LABELS[item.status]}"
            for item in results.recipients
        ]
    publication = data.get("publication")
    status_text = (
        "Loading delivery results…"
        if data.get("loading_results")
        else "Queued for delivery; sent means the bot received Telegram's acceptance."
        if publication is not None
        else "No broadcast has been queued."
    )
    return {
        "feedback": feedback_block(
            error=str(data.get("error", "")),
            warning=str(data.get("warning", "")),
        ),
        "text_heading": text_page.heading,
        "text_page": text_page.content,
        "has_text": bool(str(data.get("text", "")).strip()),
        "has_previous_text_page": text_page.has_previous,
        "has_next_text_page": text_page.has_next,
        "preview_counts": (
            f"Recipients: {preview.recipient_count}; "
            f"routable: {preview.eligible_count}; "
            f"unsendable: {preview.unsendable_count}."
            if preview is not None
            else ""
        ),
        "can_send": preview is not None and not data.get("submitting"),
        "can_back": not data.get("submitting"),
        "status_text": status_text,
        "result_counts": (
            _format_counts(results)
            if results is not None
            else "Delivery results are not available yet."
        ),
        "recipient_lines": (
            "\n".join(recipient_lines)
            if recipient_lines
            else "(No recipient results on this page.)"
        ),
        "result_page": (
            f"Page {results.skip // RESULT_PAGE_SIZE + 1} of "
            f"{max(1, (results.total + RESULT_PAGE_SIZE - 1) // RESULT_PAGE_SIZE)}"
            if results is not None
            else ""
        ),
        "has_previous_results": results is not None and results.skip > 0,
        "has_next_results": (
            results is not None
            and results.skip + len(results.recipients) < results.total
        ),
        "outcome_unknown": bool(data.get("outcome_unknown")),
        "outcome_missing": bool(data.get("outcome_missing")),
        "loading_results": bool(data.get("loading_results")),
        "operation_key": escape(str(data.get("operation_key") or "")),
        "publication": publication is not None,
    }


def _format_counts(results: TeacherBroadcastResults) -> str:
    counts = results.counts
    return (
        f"Pending: {counts.pending}; retrying: {counts.retrying}; "
        f"sent: {counts.sent}; suppressed: {counts.suppressed}; "
        f"failed: {counts.failed}; unknown: {counts.unknown}."
    )


def build_broadcast_dialog() -> Dialog:
    return Dialog(
        Window(
            Format(
                "{feedback}Compose a plain-text message (1–4096 characters), then "
                "preview the exact text and recipient counts.\n"
                "{text_heading}{text_page}"
            ),
            TextInput(
                "broadcast_text",
                type_factory=_validate_broadcast_text,
                on_success=set_broadcast_text,
                on_error=broadcast_text_error,
            ),
            Row(
                Button(
                    Const("Previous message"),
                    id="previous_text",
                    on_click=previous_text_page,
                    when="has_previous_text_page",
                ),
                Button(
                    Const("Next message"),
                    id="next_text",
                    on_click=next_text_page,
                    when="has_next_text_page",
                ),
            ),
            Button(
                Const("Preview"),
                id="preview",
                on_click=preview_broadcast,
                when="has_text",
            ),
            Button(Const("Cancel"), id="cancel", on_click=cancel_broadcast),
            state=BroadcastScreens.compose,
        ),
        Window(
            Format(
                "{feedback}Preview only — nothing has been sent.\n"
                "{preview_counts}\n{text_heading}{text_page}"
            ),
            Row(
                Button(
                    Const("Previous message"),
                    id="previous_text",
                    on_click=previous_text_page,
                    when="has_previous_text_page",
                ),
                Button(
                    Const("Next message"),
                    id="next_text",
                    on_click=next_text_page,
                    when="has_next_text_page",
                ),
            ),
            Button(
                Const("Confirm broadcast"),
                id="confirm",
                on_click=confirm_broadcast,
                when="can_send",
            ),
            Button(
                Const("Back"),
                id="back",
                on_click=back_to_compose,
                when="can_back",
            ),
            Button(
                Const("Cancel"),
                id="cancel",
                on_click=cancel_broadcast,
                when="can_back",
            ),
            state=BroadcastScreens.review,
        ),
        Window(
            Format(
                "{feedback}The publish outcome is unknown. Do not start a new "
                "broadcast. Check or retry this exact operation.\n"
                "Operation key: {operation_key}"
            ),
            Button(
                Const("Check publication status"),
                id="check_status",
                on_click=reconcile_pending_broadcast,
                when="outcome_unknown",
            ),
            Button(
                Const("Retry same operation"),
                id="retry_same",
                on_click=retry_pending_broadcast,
                when="outcome_missing",
            ),
            state=BroadcastScreens.outcome_unknown,
        ),
        Window(
            Format(
                "{feedback}{status_text}\n{result_counts}\n"
                "{result_page}\n{recipient_lines}"
            ),
            Row(
                Button(
                    Const("Previous results"),
                    id="previous_results",
                    on_click=previous_result_page,
                    when="has_previous_results",
                ),
                Button(
                    Const("Next results"),
                    id="next_results",
                    on_click=next_result_page,
                    when="has_next_results",
                ),
            ),
            Button(
                Const("Refresh results"),
                id="refresh_results",
                on_click=refresh_broadcast_results,
                when="publication",
            ),
            Button(Const("Back to home"), id="home", on_click=back_to_home),
            state=BroadcastScreens.results,
        ),
        getter=broadcast_data,
        on_start=broadcast_started,
    )

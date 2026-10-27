from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.auth.utils import create_access_token
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.models import Homework, HomeworkAttachment, HomeworkSubmission
from backend.main import app
from telegram.api import BackendClient
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver, rendered_utf16_length

TEACHER_TELEGRAM_ID = 5_720_000_001
STUDENT_TELEGRAM_ID = TEACHER_TELEGRAM_ID + 1
OUTSIDER_TELEGRAM_ID = TEACHER_TELEGRAM_ID + 10
Forward = Callable[[httpx.Request], Awaitable[httpx.Response]]


async def _seed(session: AsyncSession) -> tuple[User, User, User, User, User]:
    teacher = User(
        id=uuid4(),
        full_name="Homework Teacher",
        role="TEACHER",
        timezone="UTC",
        telegram_user_id=TEACHER_TELEGRAM_ID,
        telegram_private_chat_id=TEACHER_TELEGRAM_ID,
        is_active=True,
    )
    student = User(
        id=uuid4(),
        full_name="Alice Learner",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
        telegram_user_id=STUDENT_TELEGRAM_ID,
        telegram_private_chat_id=STUDENT_TELEGRAM_ID,
        is_active=True,
    )
    second_student = User(
        id=uuid4(),
        full_name="Bob Learner",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
        telegram_user_id=STUDENT_TELEGRAM_ID + 1,
        telegram_private_chat_id=STUDENT_TELEGRAM_ID + 1,
        is_active=True,
    )
    outsider_teacher = User(
        id=uuid4(),
        full_name="Other Teacher",
        role="TEACHER",
        timezone="UTC",
        telegram_user_id=OUTSIDER_TELEGRAM_ID,
        telegram_private_chat_id=OUTSIDER_TELEGRAM_ID,
        is_active=True,
    )
    outsider_student = User(
        id=uuid4(),
        full_name="Other Student",
        role="STUDENT",
        timezone="UTC",
        teacher_id=outsider_teacher.id,
        telegram_user_id=OUTSIDER_TELEGRAM_ID + 1,
        telegram_private_chat_id=OUTSIDER_TELEGRAM_ID + 1,
        is_active=True,
    )
    profiles = [
        TeacherProfile(
            teacher_id=item.id,
            code=f"t_{code}",
            work_start=None,
            work_end=None,
            working_days=[],
            lesson_length=60,
            between_lessons_break=5,
            max_consecutive_lessons=4,
            max_lessons_per_day=6,
        )
        for item, code in ((teacher, "HMANA"), (outsider_teacher, "HMANB"))
    ]
    session.add_all(
        [
            teacher,
            student,
            second_student,
            outsider_teacher,
            outsider_student,
            *profiles,
            StudentProfile(student_id=student.id),
            StudentProfile(student_id=second_student.id),
            StudentProfile(student_id=outsider_student.id),
        ]
    )
    await session.commit()
    return teacher, student, second_student, outsider_teacher, outsider_student


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


async def _create_assignment(
    client: httpx.AsyncClient,
    teacher: User,
    student: User,
    *,
    text: str,
    material: tuple[str, bytes] | None = None,
) -> UUID:
    attachment_ids: list[str] = []
    if material is not None:
        staged = await client.post(
            "/api/v1/homework/attachments",
            headers={**_auth(teacher), "Idempotency-Key": str(uuid4())},
            files={"file": material},
        )
        assert staged.status_code == 201, staged.text
        attachment_ids.append(staged.json()["id"])
    response = await client.post(
        "/api/v1/homework/assignments",
        headers={**_auth(teacher), "Idempotency-Key": str(uuid4())},
        json={
            "student_id": str(student.id),
            "text": text,
            "attachment_ids": attachment_ids,
        },
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def _submit(
    client: httpx.AsyncClient,
    assignment_id: UUID,
    student: User,
    *,
    text: str,
    material: tuple[str, bytes] | None = None,
) -> None:
    attachment_ids: list[str] = []
    if material is not None:
        staged = await client.post(
            "/api/v1/homework/attachments",
            headers={**_auth(student), "Idempotency-Key": str(uuid4())},
            files={"file": material},
        )
        assert staged.status_code == 201, staged.text
        attachment_ids.append(staged.json()["id"])
    response = await client.post(
        f"/api/v1/homework/assignments/{assignment_id}/submissions",
        headers=_auth(student),
        json={
            "text": text,
            "attachment_ids": attachment_ids,
            "operation_key": str(uuid4()),
        },
    )
    assert response.status_code == 201, response.text


async def _make_driver(
    client: httpx.AsyncClient,
    bot_service_key: str,
    *,
    telegram_id: int = TEACHER_TELEGRAM_ID,
    forward: Forward | None = None,
) -> tuple[BotDriver, httpx.AsyncClient]:
    async def relay(request: httpx.Request) -> httpx.Response:
        if forward is not None:
            return await forward(request)
        return await client.send(request)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(relay), base_url="http://test"
    )
    driver = BotDriver(
        build_dispatcher(BackendClient(http, bot_service_key)),
        user_id=telegram_id,
    )
    return driver, http


def _button_with_prefix(driver: BotDriver, prefix: str) -> str:
    assert driver.session.last_message is not None
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    labels = [button.text for row in markup.inline_keyboard for button in row]
    match = next((label for label in labels if prefix in label), None)
    assert match is not None, f"No button contains {prefix!r}: {labels!r}"
    return match


async def _close(driver: BotDriver, http: httpx.AsyncClient) -> None:
    await driver.close()
    await http.aclose()


@pytest.fixture
def homework_storage(tmp_path: Path) -> Path:
    app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
        STORAGE_PATH=tmp_path
    )
    return tmp_path


async def test_teacher_reviews_versions_downloads_completes_and_deletes(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    homework_storage: Path,
) -> None:
    teacher, student, second_student, outsider_teacher, outsider_student = await _seed(
        db_session
    )
    reviewed_id = await _create_assignment(
        client,
        teacher,
        student,
        text="Review the essay",
        material=("prompt.pdf", b"prompt bytes"),
    )
    await _submit(
        client,
        reviewed_id,
        student,
        text="First draft",
        material=("draft.pdf", b"draft bytes"),
    )
    await _submit(client, reviewed_id, student, text="Revised draft")
    complete_id = await _create_assignment(
        client, teacher, student, text="Mark this complete"
    )
    second_student_id = await _create_assignment(
        client, teacher, second_student, text="Bob's work"
    )
    outsider_id = await _create_assignment(
        client, outsider_teacher, outsider_student, text="Private other work"
    )
    driver, http = await _make_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await driver.click("Manage homework")
        await driver.click("Alice Learner")
        assert _button_with_prefix(driver, "Review the essay")
        assignment_buttons = [
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        ]
        assert all("Bob's work" not in label for label in assignment_buttons)
        assert all("Private other work" not in label for label in assignment_buttons)
        await driver.click(_button_with_prefix(driver, "Review the essay"))
        assert "Version 2 (current)" in driver.session.last_message.text
        assert "Revised draft" in driver.session.last_message.text
        await driver.click("Version 1")
        assert "First draft" in driver.session.last_message.text

        screen_id = driver.session.last_message.message_id
        await driver.click(_button_with_prefix(driver, "Assignment: prompt.pdf"))
        await driver.click("Version 1")
        await driver.click(_button_with_prefix(driver, "Solution v1: draft.pdf"))
        assert driver.session.sent_documents == [
            (TEACHER_TELEGRAM_ID, "prompt.pdf", b"prompt bytes"),
            (TEACHER_TELEGRAM_ID, "draft.pdf", b"draft bytes"),
        ]
        assert driver.session.last_message.message_id == screen_id
        driver.session.document_failure = "simulated document rejection"
        await driver.click(_button_with_prefix(driver, "Assignment: prompt.pdf"))
        assert "Telegram could not send this file" in driver.session.last_message.text
        assert driver.session.last_message.message_id == screen_id

        await driver.click("Back to list")
        await driver.click(_button_with_prefix(driver, "Mark this complete"))
        await driver.click("Mark complete")
        assert "Marked complete by teacher" in driver.session.last_message.text
        completed = await db_session.get(Homework, complete_id)
        assert completed is not None
        assert completed.status == "COMPLETE"
        assert completed.completion_source == "TEACHER"

        await driver.click("Back to list")
        await driver.click("Complete")
        await driver.click("Refresh")
        assert "COMPLETE" in driver.session.last_message.text
        await driver.click(_button_with_prefix(driver, "Mark this complete"))
        await driver.click("Delete assignment")
        await driver.click("Cancel")
        assert (
            await db_session.scalar(
                select(Homework.id).where(Homework.id == complete_id)
            )
            == complete_id
        )
        await driver.click("Delete assignment")
        await driver.click("Confirm delete")
        assert (
            await db_session.scalar(
                select(Homework.id).where(Homework.id == complete_id)
            )
            is None
        )
        assert "deleted" in driver.session.last_message.text.lower()

        await driver.click("Change student")
        await driver.click("Bob Learner")
        assert "Bob&#x27;s work" in _button_with_prefix(driver, "Bob&#x27;s work")
        assert all(
            "Review the essay" not in button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        )
        assert await db_session.get(Homework, second_student_id) is not None
        assert await db_session.get(Homework, outsider_id) is not None
        submission = await db_session.scalar(
            select(HomeworkSubmission)
            .where(HomeworkSubmission.homework_id == reviewed_id)
            .order_by(HomeworkSubmission.version.desc())
        )
        assert submission is not None and submission.version == 2
        material = await db_session.scalar(
            select(HomeworkAttachment).where(
                HomeworkAttachment.submission_id.is_not(None),
                HomeworkAttachment.file_name == "draft.pdf",
            )
        )
        assert material is not None
        assert (homework_storage / material.storage_key).read_bytes() == b"draft bytes"
    finally:
        await _close(driver, http)
        app.dependency_overrides.pop(get_homework_settings, None)


async def test_long_management_text_paginates_with_download_error_and_delete_controls(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    homework_storage: Path,
) -> None:
    teacher, student, _second, _outsider_teacher, _outsider_student = await _seed(
        db_session
    )
    assignment_text = "Read 🚀 " * 1200
    submission_text = "Answer 🚀 " * 1200
    assignment_id = await _create_assignment(
        client,
        teacher,
        student,
        text=assignment_text,
        material=("worksheet.pdf", b"private worksheet"),
    )
    await _submit(
        client,
        assignment_id,
        student,
        text=submission_text,
        material=("solution.pdf", b"private solution"),
    )
    assert homework_storage.is_dir()

    async def unavailable_download(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/attachments/" in request.url.path:
            return httpx.Response(
                503, json={"detail": "storage temporarily unavailable"}
            )
        return await client.send(request)

    driver, http = await _make_driver(
        client, bot_service_key, forward=unavailable_download
    )
    try:
        await driver.text("/start")
        await driver.click("Manage homework")
        await driver.click("Alice Learner")
        await driver.click(_button_with_prefix(driver, "Read 🚀"))
        assert "Assignment text (page 1 of" in driver.session.last_message.text
        assert "Submission text (page 1 of" in driver.session.last_message.text
        message_id = driver.session.last_message.message_id

        await driver.click(_button_with_prefix(driver, "Assignment: worksheet.pdf"))

        visible = driver.session.last_message.text
        assert visible.startswith("<b>❌ Error</b>\n")
        assert "Assignment text (page 1 of" in visible
        assert "Submission text (page 1 of" in visible
        assert rendered_utf16_length(visible) <= 4096
        send_count = sum(
            call.__api_method__ == "sendMessage" for call in driver.session.calls
        )

        await driver.click("Next assignment text")
        visible = driver.session.last_message.text
        assert visible.startswith("<b>❌ Error</b>\n")
        assert "Assignment text (page 2 of" in visible
        assert "Submission text (page 1 of" in visible
        assert rendered_utf16_length(visible) <= 4096
        await driver.click("Next submission text")
        visible = driver.session.last_message.text
        assert "Submission text (page 2 of" in visible
        assert rendered_utf16_length(visible) <= 4096
        assert driver.session.last_message.message_id == message_id
        assert (
            sum(call.__api_method__ == "sendMessage" for call in driver.session.calls)
            == send_count
        )
        driver.callback_data(_button_with_prefix(driver, "Assignment: worksheet.pdf"))

        await driver.click("Delete assignment")
        visible = driver.session.last_message.text
        assert "Delete this assignment" in visible
        assert "Assignment text (page 2 of" in visible
        driver.callback_data("Confirm delete")
        assert rendered_utf16_length(visible) <= 4096
    finally:
        await _close(driver, http)


async def test_unknown_delete_outcome_uses_attention_until_status_is_confirmed(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, student, _second, _outsider_teacher, _outsider_student = await _seed(
        db_session
    )
    assignment_id = await _create_assignment(
        client, teacher, student, text="Delete status recovery"
    )
    lose_delete_response = True
    fail_status_check = True
    delete_requests = 0

    async def lose_delete_result(request: httpx.Request) -> httpx.Response:
        nonlocal lose_delete_response, fail_status_check, delete_requests
        if request.method == "DELETE" and request.url.path.endswith(
            f"/{assignment_id}"
        ):
            delete_requests += 1
            response = await client.send(request)
            if lose_delete_response:
                lose_delete_response = False
                return httpx.Response(503, json={"detail": "delete response lost"})
            return response
        if (
            request.method == "GET"
            and request.url.path.endswith(f"/{assignment_id}")
            and not lose_delete_response
            and fail_status_check
        ):
            fail_status_check = False
            return httpx.Response(503, json={"detail": "status check unavailable"})
        return await client.send(request)

    driver, http = await _make_driver(
        client, bot_service_key, forward=lose_delete_result
    )
    try:
        await driver.text("/start")
        await driver.click("Manage homework")
        await driver.click("Alice Learner")
        await driver.click(_button_with_prefix(driver, "Delete status recovery"))
        await driver.click("Delete assignment")
        await driver.click("Confirm delete")

        visible = driver.session.last_message.text
        assert visible.startswith("<b>⚠️ Attention</b>\n")
        assert "Check the assignment status" in visible
        assert "❌ Error" not in visible
        driver.callback_data("Check status")
        assert delete_requests == 2

        await driver.click("Check status")
        assert (
            await db_session.scalar(
                select(Homework.id).where(Homework.id == assignment_id)
            )
            is None
        )
        assert "Deletion was confirmed after refreshing" in (
            driver.session.last_message.text
        )
        assert "⚠️ Attention" not in driver.session.last_message.text
        assert delete_requests == 2
    finally:
        await _close(driver, http)


async def test_teacher_reconciles_ambiguous_completion_and_delete(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, student, _second, _outsider_teacher, _outsider_student = await _seed(
        db_session
    )
    assignment_id = await _create_assignment(
        client, teacher, student, text="Ambiguous result"
    )
    lost_completion_response = True
    lost_delete_response = True

    async def lose_first_result(request: httpx.Request) -> httpx.Response:
        nonlocal lost_completion_response, lost_delete_response
        response = await client.send(request)
        if request.method == "POST" and request.url.path.endswith("/complete"):
            if lost_completion_response:
                lost_completion_response = False
                return httpx.Response(503, json={"detail": "response lost"})
        if request.method == "DELETE" and request.url.path.endswith(
            f"/{assignment_id}"
        ):
            if lost_delete_response:
                lost_delete_response = False
                return httpx.Response(503, json={"detail": "response lost"})
        return response

    driver, http = await _make_driver(
        client, bot_service_key, forward=lose_first_result
    )
    try:
        await driver.text("/start")
        await driver.click("Manage homework")
        await driver.click("Alice Learner")
        await driver.click(_button_with_prefix(driver, "Ambiguous result"))
        await driver.click("Mark complete")
        assert "confirmed after reloading" in driver.session.last_message.text
        assert "Marked complete by teacher" in driver.session.last_message.text
        completed = await db_session.get(Homework, assignment_id)
        assert completed is not None and completed.completion_source == "TEACHER"

        await driver.click("Delete assignment")
        await driver.click("Confirm delete")
        assert (
            await db_session.scalar(
                select(Homework.id).where(Homework.id == assignment_id)
            )
            is None
        )
        assert "confirmed after refreshing" in driver.session.last_message.text.lower()
        assert lost_completion_response is False
        assert lost_delete_response is False
    finally:
        await _close(driver, http)


async def test_homework_survives_bot_restarts_and_scopes_every_role(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    homework_storage: Path,
) -> None:
    teacher, student, second_student, outsider_teacher, outsider_student = await _seed(
        db_session
    )
    assignment_text = "Read the attached chapter"
    manual_text = "Bob separate practice"
    teacher_driver, teacher_http = await _make_driver(client, bot_service_key)
    try:
        await teacher_driver.text("/start")
        await teacher_driver.click("Homework")
        await teacher_driver.click("Alice Learner")
        await teacher_driver.text(assignment_text)
        await teacher_driver.document("chapter.pdf", b"chapter bytes")
        await teacher_driver.click("Review")
        await teacher_driver.click("Publish assignment")

        await teacher_driver.click("Homework")
        await teacher_driver.click("Bob Learner")
        await teacher_driver.text(manual_text)
        await teacher_driver.click("Review")
        await teacher_driver.click("Publish assignment")
    finally:
        await _close(teacher_driver, teacher_http)

    alice_driver, alice_http = await _make_driver(
        client, bot_service_key, telegram_id=STUDENT_TELEGRAM_ID
    )
    try:
        await alice_driver.text("/start")
        await alice_driver.click("Homework")
        await alice_driver.click(_button_with_prefix(alice_driver, assignment_text))
        screen_id = alice_driver.session.last_message.message_id
        await alice_driver.click(_button_with_prefix(alice_driver, "chapter.pdf"))
        assert alice_driver.session.sent_documents == [
            (STUDENT_TELEGRAM_ID, "chapter.pdf", b"chapter bytes")
        ]
        assert alice_driver.session.last_message.message_id == screen_id
        await alice_driver.click("Submit solution")
        await alice_driver.text("My answer")
        await alice_driver.document("answer.txt", b"answer bytes")
        await alice_driver.click("Review")
        await alice_driver.click("Finish submission")
        assert "completed the assignment" in alice_driver.session.last_message.text
    finally:
        await _close(alice_driver, alice_http)

    restarted_alice, restarted_alice_http = await _make_driver(
        client, bot_service_key, telegram_id=STUDENT_TELEGRAM_ID
    )
    try:
        await restarted_alice.text("/start")
        await restarted_alice.click("Homework")
        await restarted_alice.click(
            _button_with_prefix(restarted_alice, assignment_text)
        )
        assert "Completed by your submission" in (
            restarted_alice.session.last_message.text
        )
    finally:
        await _close(restarted_alice, restarted_alice_http)

    bob_driver, bob_http = await _make_driver(
        client, bot_service_key, telegram_id=STUDENT_TELEGRAM_ID + 1
    )
    try:
        await bob_driver.text("/start")
        await bob_driver.click("Homework")
        labels = [
            button.text
            for row in bob_driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        ]
        assert any(manual_text in label for label in labels)
        assert all(assignment_text not in label for label in labels)
    finally:
        await _close(bob_driver, bob_http)

    outsider_driver, outsider_http = await _make_driver(
        client, bot_service_key, telegram_id=OUTSIDER_TELEGRAM_ID
    )
    try:
        await outsider_driver.text("/start")
        await outsider_driver.click("Manage homework")
        labels = [
            button.text
            for row in outsider_driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        ]
        assert any("Other Student" in label for label in labels)
        assert all("Alice Learner" not in label for label in labels)
        assert all("Bob Learner" not in label for label in labels)
    finally:
        await _close(outsider_driver, outsider_http)

    requests: list[httpx.Request] = []

    async def relay(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return await client.send(request)

    teacher_api_http = httpx.AsyncClient(
        transport=httpx.MockTransport(relay), base_url="http://test"
    )
    teacher_api = BackendClient(teacher_api_http, bot_service_key)
    restarted_teacher = BotDriver(
        build_dispatcher(teacher_api), user_id=TEACHER_TELEGRAM_ID
    )
    try:
        await restarted_teacher.text("/start")
        sessions_before_retry = sum(
            request.url.path == "/api/v1/telegram/session" for request in requests
        )
        teacher_api.tokens[TEACHER_TELEGRAM_ID] = "expired-token"
        await restarted_teacher.click("Manage homework")
        sessions_after_retry = sum(
            request.url.path == "/api/v1/telegram/session" for request in requests
        )
        assert sessions_after_retry > sessions_before_retry
        await restarted_teacher.click("Alice Learner")
        await restarted_teacher.click(
            _button_with_prefix(restarted_teacher, assignment_text)
        )
        assert "My answer" in restarted_teacher.session.last_message.text
        await restarted_teacher.click(
            _button_with_prefix(restarted_teacher, "Solution v1: answer.txt")
        )
        assert restarted_teacher.session.sent_documents[-1] == (
            TEACHER_TELEGRAM_ID,
            "answer.txt",
            b"answer bytes",
        )

        await restarted_teacher.click("Back to list")
        await restarted_teacher.click("Change student")
        await restarted_teacher.click("Bob Learner")
        await restarted_teacher.click(
            _button_with_prefix(restarted_teacher, manual_text)
        )
        await restarted_teacher.click("Mark complete")
        manual_assignment = await db_session.scalar(
            select(Homework).where(Homework.text == manual_text)
        )
        assert manual_assignment is not None
        assert manual_assignment.status == "COMPLETE"
        assert manual_assignment.completion_source == "TEACHER"
        await restarted_teacher.click("Back to list")
        await restarted_teacher.click(
            _button_with_prefix(restarted_teacher, manual_text)
        )
        await restarted_teacher.click("Delete assignment")
        await restarted_teacher.click("Confirm delete")
        assert (
            await db_session.scalar(
                select(Homework.id).where(Homework.text == manual_text)
            )
            is None
        )
    finally:
        await _close(restarted_teacher, teacher_api_http)

    assignment = await db_session.scalar(
        select(Homework).where(Homework.text == assignment_text)
    )
    assert assignment is not None
    assert assignment.student_id == student.id
    assert assignment.teacher_id == teacher.id
    assert assignment.status == "COMPLETE"
    assert assignment.completion_source == "STUDENT_SUBMISSION"
    submission = await db_session.scalar(
        select(HomeworkSubmission).where(
            HomeworkSubmission.homework_id == assignment.id
        )
    )
    assert submission is not None and submission.text == "My answer"
    attachments = list(
        await db_session.scalars(
            select(HomeworkAttachment).where(
                HomeworkAttachment.homework_id == assignment.id
            )
        )
    )
    assert {item.file_name for item in attachments} == {"chapter.pdf", "answer.txt"}
    assert all((homework_storage / item.storage_key).exists() for item in attachments)
    contents = {
        item.file_name: (homework_storage / item.storage_key).read_bytes()
        for item in attachments
    }
    assert contents == {
        "chapter.pdf": b"chapter bytes",
        "answer.txt": b"answer bytes",
    }
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Homework)
            .where(
                Homework.teacher_id == outsider_teacher.id,
                Homework.student_id == outsider_student.id,
            )
        )
        == 0
    )


async def test_teacher_upload_recovers_from_interrupted_and_unavailable_storage(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    tmp_path: Path,
) -> None:
    teacher, student, _second, _outsider_teacher, _outsider_student = await _seed(
        db_session
    )
    requests: list[httpx.Request] = []
    interrupt_first_upload = True

    async def relay(request: httpx.Request) -> httpx.Response:
        nonlocal interrupt_first_upload
        requests.append(request)
        if (
            interrupt_first_upload
            and request.method == "POST"
            and request.url.path == "/api/v1/homework/attachments"
        ):
            interrupt_first_upload = False
            raise httpx.ReadTimeout("simulated interrupted upload")
        return await client.send(request)

    storage_path = tmp_path / "homework"
    app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
        STORAGE_PATH=storage_path
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(relay), base_url="http://test"
    )
    driver = BotDriver(
        build_dispatcher(BackendClient(http, bot_service_key)),
        user_id=TEACHER_TELEGRAM_ID,
    )
    try:
        await driver.text("/start")
        await driver.click("Homework")
        await driver.click("Alice Learner")
        await driver.document("interrupted.pdf", b"recoverable bytes")
        assert "outcome is unknown" in driver.session.last_message.text.lower()
        await driver.click("Retry upload: interrupted.pdf")
        await driver.text("Interrupted upload recovery")
        await driver.click("Review")
        await driver.click("Publish assignment")
        assignment = await db_session.scalar(
            select(Homework).where(Homework.text == "Interrupted upload recovery")
        )
        assert assignment is not None
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(HomeworkAttachment)
                .where(HomeworkAttachment.homework_id == assignment.id)
            )
            == 1
        )

        blocked_root = tmp_path / f"not-a-directory-{uuid4().hex}"
        blocked_root.write_text("block")
        app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
            STORAGE_PATH=blocked_root
        )
        await driver.click("Homework")
        await driver.click("Alice Learner")
        await driver.document("unavailable.pdf", b"must not persist")
        driver.callback_data("Retry upload: unavailable.pdf")
        assert await db_session.scalar(select(func.count()).select_from(Homework)) == 1
        assert (
            await db_session.scalar(
                select(func.count()).select_from(HomeworkAttachment)
            )
            == 1
        )
        app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
            STORAGE_PATH=storage_path
        )
        await driver.click("Retry upload: unavailable.pdf")
        await driver.click("Cancel")
        assert await db_session.scalar(select(func.count()).select_from(Homework)) == 1
        assert (
            await db_session.scalar(
                select(func.count()).select_from(HomeworkAttachment)
            )
            == 1
        )
        assert list(storage_path.iterdir())
        attachment_requests = [
            request
            for request in requests
            if request.method == "POST"
            and request.url.path == "/api/v1/homework/attachments"
        ]
        assert len(attachment_requests) == 4
        assert (
            attachment_requests[0].headers["Idempotency-Key"]
            == (attachment_requests[1].headers["Idempotency-Key"])
        )
        assert (
            attachment_requests[2].headers["Idempotency-Key"]
            == (attachment_requests[3].headers["Idempotency-Key"])
        )
    finally:
        await _close(driver, http)
        app.dependency_overrides.pop(get_homework_settings, None)

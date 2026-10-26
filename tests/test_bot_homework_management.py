from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.auth.utils import create_access_token
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.models import Homework, HomeworkAttachment, HomeworkSubmission
from backend.main import app
from telegram.api import BackendClient
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

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
        user_id=TEACHER_TELEGRAM_ID,
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

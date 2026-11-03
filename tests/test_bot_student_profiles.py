from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.activity.constants import StudentActionType
from backend.activity.models import StudentAction
from backend.auth.models import (
    StudentProfile,
    StudentRemovalOperation,
    StudentTeacherRelationship,
    TeacherProfile,
    User,
)
from backend.auth.utils import create_access_token
from telegram.api import BackendClient
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

TEACHER_TELEGRAM_ID = 5_730_000_001
STUDENT_TELEGRAM_ID = TEACHER_TELEGRAM_ID + 1
OUTSIDER_TELEGRAM_ID = TEACHER_TELEGRAM_ID + 10
Forward = Callable[[httpx.Request], Awaitable[httpx.Response]]


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


async def _seed_teacher_and_student(
    session: AsyncSession,
    *,
    teacher_telegram_id: int = TEACHER_TELEGRAM_ID,
    student_telegram_id: int = STUDENT_TELEGRAM_ID,
    student_name: str = "Alice Learner",
) -> tuple[User, User, StudentTeacherRelationship]:
    teacher = User(
        id=uuid4(),
        full_name="Profiles Teacher",
        role="TEACHER",
        timezone="UTC",
        telegram_user_id=teacher_telegram_id,
        telegram_private_chat_id=teacher_telegram_id,
        is_active=True,
    )
    student = User(
        id=uuid4(),
        full_name=student_name,
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
        telegram_user_id=student_telegram_id,
        telegram_private_chat_id=student_telegram_id,
        is_active=True,
    )
    relationship = StudentTeacherRelationship(
        teacher_id=teacher.id,
        student_id=student.id,
        linked_at=datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
    )
    session.add_all(
        [
            teacher,
            student,
            TeacherProfile(
                teacher_id=teacher.id,
                code=f"t_{teacher_telegram_id % 1_000_000:06d}",
                work_start=None,
                work_end=None,
                working_days=[],
                lesson_length=60,
                between_lessons_break=5,
                max_consecutive_lessons=4,
                max_lessons_per_day=6,
            ),
            StudentProfile(student_id=student.id),
            relationship,
        ]
    )
    await session.flush()
    session.add(
        StudentAction(
            relationship_id=relationship.id,
            teacher_id=teacher.id,
            student_id=student.id,
            actor_id=student.id,
            action_type=StudentActionType.ACCOUNT_PROFILE_UPDATED,
            occurred_at=datetime(2026, 2, 3, 4, 5, tzinfo=UTC),
        )
    )
    await session.commit()
    return teacher, student, relationship


async def _create_homework(
    client: httpx.AsyncClient, teacher: User, student: User, text: str
) -> UUID:
    response = await client.post(
        "/api/v1/homework/assignments",
        headers={**_auth(teacher), "Idempotency-Key": str(uuid4())},
        json={"student_id": str(student.id), "text": text, "attachment_ids": []},
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def _make_driver(
    client: httpx.AsyncClient,
    bot_service_key: str,
    telegram_id: int,
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


def _button_labels(driver: BotDriver) -> list[str]:
    assert driver.session.last_message is not None
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    return [button.text for row in markup.inline_keyboard for button in row]


async def _close(driver: BotDriver, http: httpx.AsyncClient) -> None:
    await driver.close()
    await http.aclose()


@pytest.mark.asyncio
async def test_teacher_profile_removal_and_former_homework_flow(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, student, relationship = await _seed_teacher_and_student(db_session)
    await _create_homework(client, teacher, student, "Read the retained chapter")
    reject_first_confirmation = True

    async def forward(request: httpx.Request) -> httpx.Response:
        nonlocal reject_first_confirmation
        if (
            request.method == "POST"
            and request.url.path.endswith("/removal/confirm")
            and reject_first_confirmation
        ):
            reject_first_confirmation = False
            return httpx.Response(
                409,
                json={"detail": "The removal preview is stale."},
                request=request,
            )
        return await client.send(request)

    teacher_driver, teacher_http = await _make_driver(
        client, bot_service_key, TEACHER_TELEGRAM_ID, forward=forward
    )
    forged_student_action: str | None = None
    try:
        await teacher_driver.text("/start")
        assert "Students" in _button_labels(teacher_driver)
        forged_student_action = teacher_driver.callback_data("Students")
        initial_message_id = teacher_driver.session.last_message.message_id
        send_count = sum(
            call.__api_method__ == "sendMessage"
            for call in teacher_driver.session.calls
        )

        await teacher_driver.click("Students")
        assert teacher_driver.session.last_message.message_id == initial_message_id
        assert "Page 1 of 1 · 1 student(s)" in teacher_driver.session.last_message.text
        await teacher_driver.click(_button_with_prefix(teacher_driver, "Alice Learner"))
        detail = teacher_driver.session.last_message.text
        assert "Username: No username" in detail
        assert "Added: 2026-01-02 03:04 UTC" in detail
        assert "Homework: 0/1 complete, 1 incomplete" in detail
        assert "Last 10 actions" in detail
        assert "Account profile updated" in detail

        await teacher_driver.click("View homework")
        await teacher_driver.click(
            _button_with_prefix(teacher_driver, "Read the retained")
        )
        assert "Mark complete" in _button_labels(teacher_driver)
        assert "Delete assignment" in _button_labels(teacher_driver)
        await teacher_driver.click("Back to list")
        await teacher_driver.click("Back to profile")

        await teacher_driver.click("Remove student")
        assert "Review student removal" in teacher_driver.session.last_message.text
        await teacher_driver.click("Confirm removal")
        assert "impact changed" in teacher_driver.session.last_message.text
        assert "Alice Learner" in teacher_driver.session.last_message.text

        await teacher_driver.click(_button_with_prefix(teacher_driver, "Alice Learner"))
        await teacher_driver.click("Remove student")
        await teacher_driver.click("Cancel")
        assert "Alice Learner" in teacher_driver.session.last_message.text
        await teacher_driver.click("Remove student")
        await teacher_driver.click("Confirm removal")
        assert (
            "Student removed from this teacher"
            in teacher_driver.session.last_message.text
        )

        await db_session.refresh(student)
        await db_session.refresh(relationship)
        assert student.is_active is True
        assert student.teacher_id is None
        assert relationship.removed_at is not None

        await teacher_driver.click("Former students")
        await teacher_driver.click(_button_with_prefix(teacher_driver, "Alice Learner"))
        former_detail = teacher_driver.session.last_message.text
        assert "Removed from this teacher:" in former_detail
        assert "This former-student profile is read-only." in former_detail
        await teacher_driver.click("View homework")
        assert "Former-student homework is read-only." in (
            teacher_driver.session.last_message.text
        )
        await teacher_driver.click(
            _button_with_prefix(teacher_driver, "Read the retained")
        )
        labels = _button_labels(teacher_driver)
        assert "Former-student homework is read-only." in (
            teacher_driver.session.last_message.text
        )
        assert "Mark complete" not in labels
        assert "Delete assignment" not in labels
        await teacher_driver.click("Back to list")
        await teacher_driver.click("Back to profile")
        assert "This former-student profile is read-only." in (
            teacher_driver.session.last_message.text
        )
        assert (
            sum(
                call.__api_method__ == "sendMessage"
                for call in teacher_driver.session.calls
            )
            == send_count
        )
    finally:
        await _close(teacher_driver, teacher_http)

    student_driver, student_http = await _make_driver(
        client, bot_service_key, STUDENT_TELEGRAM_ID
    )
    try:
        await student_driver.text("/start")
        assert "Students" not in _button_labels(student_driver)
        assert forged_student_action is not None
        await student_driver.click(data=forged_student_action)
        assert "Students" not in _button_labels(student_driver)
    finally:
        await _close(student_driver, student_http)


@pytest.mark.asyncio
async def test_teacher_student_profile_lists_paginate(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, first_student, _relationship = await _seed_teacher_and_student(
        db_session,
        teacher_telegram_id=OUTSIDER_TELEGRAM_ID,
        student_telegram_id=OUTSIDER_TELEGRAM_ID + 1,
        student_name="Learner 00",
    )
    for index in range(1, 21):
        student = User(
            id=uuid4(),
            role="STUDENT",
            timezone="UTC",
            teacher_id=teacher.id,
            full_name=f"Learner {index:02}",
        )
        db_session.add(
            StudentTeacherRelationship(
                teacher_id=teacher.id,
                student_id=student.id,
                linked_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        )
        db_session.add(student)
    await db_session.commit()

    driver, driver_http = await _make_driver(
        client, bot_service_key, OUTSIDER_TELEGRAM_ID
    )
    try:
        await driver.text("/start")
        await driver.click("Students")
        assert "Page 1 of 2 · 21 student(s)" in driver.session.last_message.text
        await driver.click("Next")
        assert "Page 2 of 2 · 21 student(s)" in driver.session.last_message.text
        assert any("Learner 20" in label for label in _button_labels(driver))
        await driver.click("Previous")
        assert "Page 1 of 2 · 21 student(s)" in driver.session.last_message.text
        assert first_student.full_name in driver.session.last_message.text
    finally:
        await _close(driver, driver_http)


@pytest.mark.asyncio
async def test_ambiguous_student_removal_retries_same_operation(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, student, relationship = await _seed_teacher_and_student(
        db_session,
        teacher_telegram_id=OUTSIDER_TELEGRAM_ID,
        student_telegram_id=OUTSIDER_TELEGRAM_ID + 1,
        student_name="Retry Learner",
    )
    lost_first_response = True
    operation_keys: list[str] = []

    async def forward(request: httpx.Request) -> httpx.Response:
        nonlocal lost_first_response
        if request.method == "POST" and request.url.path.endswith("/removal/confirm"):
            operation_keys.append(request.headers["Idempotency-Key"])
            response = await client.send(request)
            if lost_first_response:
                lost_first_response = False
                raise httpx.ReadTimeout("The confirmation response was lost")
            return response
        return await client.send(request)

    driver, driver_http = await _make_driver(
        client, bot_service_key, OUTSIDER_TELEGRAM_ID, forward=forward
    )
    try:
        await driver.text("/start")
        await driver.click("Students")
        await driver.click(_button_with_prefix(driver, "Retry Learner"))
        await driver.click("Remove student")
        await driver.click("Confirm removal")
        assert "outcome is unknown" in driver.session.last_message.text.lower()
        assert "Retry same confirmation" in _button_labels(driver)
        await db_session.refresh(student)
        assert student.teacher_id is None
        await driver.click("Retry same confirmation")
        assert "Student removed from this teacher" in driver.session.last_message.text
        assert len(operation_keys) == 2
        assert operation_keys[0] == operation_keys[1]
        operation_count = await db_session.scalar(
            select(func.count())
            .select_from(StudentRemovalOperation)
            .where(StudentRemovalOperation.relationship_id == relationship.id)
        )
        assert operation_count == 1
    finally:
        await _close(driver, driver_http)

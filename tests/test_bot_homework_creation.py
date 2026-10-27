import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import telegram.homework_teacher_dialogs as homework_dialogs
from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.auth.utils import create_access_token
from backend.constants import EventType
from backend.events.models import Event, RecurrentEvent
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.models import Homework, HomeworkAttachment
from backend.main import app
from backend.schedule.router import get_schedule_clock
from telegram.api import (
    MAX_HOMEWORK_ATTACHMENT_BYTES,
    BackendClient,
    BackendUnavailable,
    HomeworkAssignment,
)
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

TEACHER_TELEGRAM_ID = 5_700_000_001
STUDENT_TELEGRAM_ID = TEACHER_TELEGRAM_ID + 1
FORWARD = Callable[[httpx.Request], Awaitable[httpx.Response]]


async def _seed(
    session: AsyncSession, *, student_count: int = 1
) -> tuple[User, list[User]]:
    teacher = User(
        id=uuid4(),
        full_name="Homework Teacher",
        role="TEACHER",
        timezone="UTC",
        telegram_user_id=TEACHER_TELEGRAM_ID,
        telegram_private_chat_id=TEACHER_TELEGRAM_ID,
        is_active=True,
    )
    students = [
        User(
            id=uuid4(),
            full_name="Alice Learner" if index == 0 else f"Student {index - 1:02d}",
            role="STUDENT",
            timezone="UTC",
            teacher_id=teacher.id,
            telegram_user_id=STUDENT_TELEGRAM_ID + index,
            telegram_private_chat_id=STUDENT_TELEGRAM_ID + index,
            is_active=True,
        )
        for index in range(student_count)
    ]
    session.add_all(
        [
            teacher,
            *students,
            TeacherProfile(
                teacher_id=teacher.id,
                code="t_HOMWRK",
                work_start=None,
                work_end=None,
                working_days=[],
                lesson_length=60,
                between_lessons_break=5,
                max_consecutive_lessons=4,
                max_lessons_per_day=6,
            ),
            *(StudentProfile(student_id=student.id) for student in students),
        ]
    )
    await session.commit()
    return teacher, students


async def _make_driver(
    client: httpx.AsyncClient,
    bot_service_key: str,
    telegram_id: int = TEACHER_TELEGRAM_ID,
    forward: FORWARD | None = None,
) -> tuple[BotDriver, httpx.AsyncClient, BackendClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    async def relay(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if forward is not None:
            return await forward(request)
        return await client.send(request)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(relay), base_url="http://test"
    )
    api = BackendClient(http, bot_service_key)
    driver = BotDriver(build_dispatcher(api), user_id=telegram_id)
    return driver, http, api, requests


async def _close_driver(driver: BotDriver, http: httpx.AsyncClient) -> None:
    await driver.close()
    await http.aclose()


async def _open_assignment(driver: BotDriver, student_label: str) -> None:
    await driver.click("Homework")
    await driver.click(student_label)


async def _student_assignments(
    api: BackendClient, student: User
) -> list[HomeworkAssignment]:
    if student.telegram_user_id is None or student.telegram_private_chat_id is None:
        raise AssertionError("Seeded student must have a Telegram identity")
    api.tokens[student.telegram_user_id] = create_access_token(str(student.id))
    page = await api.read_homework_assignments(
        student.telegram_user_id, student.telegram_private_chat_id
    )
    return page.items


async def test_teacher_publishes_text_file_and_mixed_assignments(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    tmp_path: Path,
) -> None:
    app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
        STORAGE_PATH=tmp_path
    )
    _teacher, students = await _seed(db_session)
    student = students[0]
    driver, http, api, _requests = await _make_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        original_message_id = driver.session.last_message.message_id
        await _open_assignment(driver, "Alice Learner")
        await driver.text("Read chapter 3")
        await driver.click("Review")
        assert "Read chapter 3" in driver.session.last_message.text
        await driver.click("Publish assignment")

        await _open_assignment(driver, "Alice Learner")
        await driver.document("worksheet.pdf", b"worksheet contents")
        assert "worksheet.pdf" in driver.session.last_message.text
        await driver.click("Review")
        await driver.click("Publish assignment")

        await _open_assignment(driver, "Alice Learner")
        await driver.text("Complete the worksheet")
        await driver.document("page-one.pdf", b"page one", media_group_id="album-1")
        await driver.document("page-two.pdf", b"page two", media_group_id="album-1")
        await driver.click("Review")
        assert "Complete the worksheet" in driver.session.last_message.text
        await driver.click("Publish assignment")

        assignments = await _student_assignments(api, student)
        assert len(assignments) == 3
        assert {assignment.text for assignment in assignments} == {
            "Read chapter 3",
            None,
            "Complete the worksheet",
        }
        if student.telegram_user_id is None or student.telegram_private_chat_id is None:
            raise AssertionError("Seeded student must have a Telegram identity")
        details = [
            await api.read_homework_assignment(
                student.telegram_user_id,
                student.telegram_private_chat_id,
                assignment.id,
            )
            for assignment in assignments
        ]
        assert sorted(len(detail.materials) for detail in details) == [0, 1, 2]
        assert {
            material.file_name for detail in details for material in detail.materials
        } == {"worksheet.pdf", "page-one.pdf", "page-two.pdf"}
        assert driver.session.last_message.message_id == original_message_id
        assert (
            sum(call.__api_method__ == "sendMessage" for call in driver.session.calls)
            == 1
        )
    finally:
        await _close_driver(driver, http)
        app.dependency_overrides.pop(get_homework_settings, None)


async def test_homework_picker_is_teacher_only_and_paginated(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    _teacher, students = await _seed(db_session, student_count=21)
    driver, http, _api, _requests = await _make_driver(client, bot_service_key)
    student_driver, student_http, _student_api, _student_requests = await _make_driver(
        client, bot_service_key, students[0].telegram_user_id
    )
    try:
        await driver.text("/start")
        await driver.click("Homework")
        assert "Page 1 of 2 (21 students)" in driver.session.last_message.text
        driver.callback_data("Next")
        await driver.click("Next")
        student_choice = next(
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
            if "Student 19" in button.text
        )
        await driver.click(student_choice)
        assert "Student 19" in driver.session.last_message.text
        await driver.click("Cancel")

        await student_driver.text("/start")
        markup = student_driver.session.last_message.reply_markup
        assert markup is not None
        assert any(
            button.text == "Homework"
            for row in markup.inline_keyboard
            for button in row
        )
    finally:
        await _close_driver(driver, http)
        await _close_driver(student_driver, student_http)


async def test_assignment_can_link_exact_upcoming_lesson_occurrence(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    monkeypatch,
) -> None:
    teacher, students = await _seed(db_session)
    student = students[0]
    occurrence_start = datetime(2030, 11, 5, 9)
    lesson = Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=occurrence_start,
        end=datetime(2030, 11, 5, 10),
        post_break_minutes=0,
    )
    db_session.add(lesson)
    recurrent_lesson = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=datetime(2030, 11, 5, 11),
        end=datetime(2030, 11, 5, 12),
        interval=7,
        interval_end=datetime(2030, 11, 26, 11),
        post_break_minutes=0,
        recurrence_mode="TEACHER_LOCAL",
        recurrence_timezone="UTC",
    )
    db_session.add(recurrent_lesson)
    await db_session.commit()
    now = datetime(2030, 11, 4, 12, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(homework_dialogs, "datetime", FixedDateTime)
    monkeypatch.setitem(app.dependency_overrides, get_schedule_clock, lambda: now)
    driver, http, api, requests = await _make_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await _open_assignment(driver, "Alice Learner")
        await driver.text("Prepare for the lesson")
        await driver.click("Link or change lesson")
        assert "Week: Mon, Nov 4 – Sun, Nov 10" in driver.session.last_message.text
        assert "Timezone: UTC" in driver.session.last_message.text
        assert "Options " not in driver.session.last_message.text
        lesson_choice = next(
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
            if "Tue, Nov 5 9:00 (event)" in button.text
        )
        await driver.click(lesson_choice)
        assert "Tue, Nov 5 9:00 (event)" in driver.session.last_message.text
        assert "2030-11-05" not in driver.session.last_message.text
        assert "(UTC" not in driver.session.last_message.text
        await driver.click("Review")
        await driver.click("Publish assignment")

        assignments = await _student_assignments(api, student)
        assert len(assignments) == 1
        linked_lesson = assignments[0].lesson_occurrence
        assert linked_lesson is not None
        assert linked_lesson.kind == "EVENT"
        assert linked_lesson.source_id == lesson.id
        assert linked_lesson.starts_at == now.replace(
            year=occurrence_start.year,
            month=occurrence_start.month,
            day=occurrence_start.day,
            hour=occurrence_start.hour,
            minute=occurrence_start.minute,
        )
        assignment_writes = [
            request
            for request in requests
            if request.method == "POST"
            and request.url.path == "/api/v1/homework/assignments"
        ]
        assert len(assignment_writes) == 1
        one_off_payload = json.loads(assignment_writes[0].content)
        assert one_off_payload["lesson_occurrence"]["kind"] == "EVENT"
        assert one_off_payload["lesson_occurrence"]["source_id"] == str(lesson.id)
        assert datetime.fromisoformat(
            one_off_payload["lesson_occurrence"]["starts_at"]
        ) == datetime(2030, 11, 5, 9, tzinfo=UTC)

        await _open_assignment(driver, "Alice Learner")
        await driver.text("Review this recurring lesson")
        await driver.click("Link or change lesson")
        recurring_choice = next(
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
            if "Tue, Nov 5 11:00 (recurrent event)" in button.text
        )
        await driver.click(recurring_choice)
        await driver.click("Review")
        await driver.click("Publish assignment")

        assignments = await _student_assignments(api, student)
        recurrent_assignment = next(
            item for item in assignments if item.text == "Review this recurring lesson"
        )
        linked_occurrence = recurrent_assignment.lesson_occurrence
        assert linked_occurrence is not None
        assert linked_occurrence.kind == "RECURRENT_EVENT"
        assert linked_occurrence.source_id == recurrent_lesson.id
        assert linked_occurrence.starts_at == datetime(2030, 11, 5, 11, tzinfo=UTC)
        assignment_writes = [
            request
            for request in requests
            if request.method == "POST"
            and request.url.path == "/api/v1/homework/assignments"
        ]
        assert len(assignment_writes) == 2
        recurring_payload = json.loads(assignment_writes[1].content)
        assert recurring_payload["lesson_occurrence"]["kind"] == "RECURRENT_EVENT"
        assert recurring_payload["lesson_occurrence"]["source_id"] == str(
            recurrent_lesson.id
        )
        assert datetime.fromisoformat(
            recurring_payload["lesson_occurrence"]["starts_at"]
        ) == datetime(2030, 11, 5, 11, tzinfo=UTC)
    finally:
        await _close_driver(driver, http)


async def test_lesson_picker_fallback_week_label_and_week_navigation(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    monkeypatch,
) -> None:
    await _seed(db_session)
    now = datetime(2030, 11, 4, 12, tzinfo=UTC)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    async def unavailable_schedule_week(
        _client: BackendClient, _sender_id: int, _chat_id: int, _requested_date=None
    ) -> None:
        raise BackendUnavailable()

    monkeypatch.setattr(homework_dialogs, "datetime", FixedDateTime)
    monkeypatch.setattr(BackendClient, "read_schedule_week", unavailable_schedule_week)
    monkeypatch.setitem(app.dependency_overrides, get_schedule_clock, lambda: now)
    driver, http, _api, _requests = await _make_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await _open_assignment(driver, "Alice Learner")
        await driver.click("Link or change lesson")
        assert "Week: Mon, Nov 4 – Sun, Nov 10" in driver.session.last_message.text
        assert "No upcoming lessons" not in driver.session.last_message.text
        assert "2030-11-" not in driver.session.last_message.text
        assert "Options " not in driver.session.last_message.text

        await driver.click("Later week")
        assert "Week: Mon, Nov 11 – Sun, Nov 17" in driver.session.last_message.text
        await driver.click("Earlier week")
        assert "Week: Mon, Nov 4 – Sun, Nov 10" in driver.session.last_message.text
    finally:
        await _close_driver(driver, http)


async def test_lesson_picker_option_pagination_stays_bounded_without_page_count(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    monkeypatch,
) -> None:
    teacher, students = await _seed(db_session)
    student = students[0]
    now = datetime(2030, 11, 4, 12, tzinfo=UTC)
    starts = [datetime(2030, 11, 5, 8) + timedelta(hours=index) for index in range(11)]
    db_session.add_all(
        [
            Event(
                teacher_id=teacher.id,
                user_id=student.id,
                event_type=EventType.LESSON.value,
                start=start,
                end=start + timedelta(minutes=30),
                post_break_minutes=0,
            )
            for start in starts
        ]
    )
    await db_session.commit()

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(homework_dialogs, "datetime", FixedDateTime)
    monkeypatch.setitem(app.dependency_overrides, get_schedule_clock, lambda: now)
    driver, http, _api, _requests = await _make_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await _open_assignment(driver, "Alice Learner")
        await driver.click("Link or change lesson")
        assert "Week: Mon, Nov 4 – Sun, Nov 10" in driver.session.last_message.text
        assert "Options " not in driver.session.last_message.text
        option_labels = [
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
            if button.text.endswith("(event)")
        ]
        assert len(option_labels) == 10
        assert "Tue, Nov 5 8:00 (event)" in option_labels

        await driver.click("Next options")
        next_page_labels = [
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
            if button.text.endswith("(event)")
        ]
        assert next_page_labels == ["Tue, Nov 5 18:00 (event)"]
        assert "Options " not in driver.session.last_message.text

        await driver.click("Previous options")
        first_page_labels = [
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
            if button.text.endswith("(event)")
        ]
        assert len(first_page_labels) == 10
        await driver.click(first_page_labels[0])
        assert "Lesson: Tue, Nov 5 8:00 (event)" in driver.session.last_message.text
    finally:
        await _close_driver(driver, http)


async def test_cancel_removes_staged_files_and_oversized_files_are_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    tmp_path: Path,
) -> None:
    app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
        STORAGE_PATH=tmp_path
    )
    _teacher, _students = await _seed(db_session)
    driver, http, api, _requests = await _make_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await _open_assignment(driver, "Alice Learner")
        for index in range(5):
            await driver.document(f"draft-{index}.pdf", f"draft {index}".encode())
        staged_count = await db_session.scalar(
            select(func.count()).select_from(HomeworkAttachment)
        )
        assert staged_count == 5
        get_file_count = sum(
            call.__api_method__ == "getFile" for call in driver.session.calls
        )
        await driver.document("sixth.pdf", b"exceeds count")
        assert "at most 5 files" in driver.session.last_message.text
        assert (
            sum(call.__api_method__ == "getFile" for call in driver.session.calls)
            == get_file_count
        )
        await driver.click("Cancel")
        staged_count = await db_session.scalar(
            select(func.count()).select_from(HomeworkAttachment)
        )
        assignment_count = await db_session.scalar(
            select(func.count()).select_from(Homework)
        )
        assert staged_count == assignment_count == 0
        assert not list(tmp_path.iterdir())

        await _open_assignment(driver, "Alice Learner")
        get_file_count = sum(
            call.__api_method__ == "getFile" for call in driver.session.calls
        )
        await driver.document(
            "too-large.pdf",
            b"x",
            advertised_size=MAX_HOMEWORK_ATTACHMENT_BYTES + 1,
        )
        assert "20 MiB" in driver.session.last_message.text
        assert (
            sum(call.__api_method__ == "getFile" for call in driver.session.calls)
            == get_file_count
        )
        await driver.click("Cancel")
        assert await _student_assignments(api, _students[0]) == []
    finally:
        await _close_driver(driver, http)
        app.dependency_overrides.pop(get_homework_settings, None)


async def test_upload_failure_does_not_publish_assignment(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    tmp_path: Path,
) -> None:
    app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
        STORAGE_PATH=tmp_path
    )
    _teacher, students = await _seed(db_session)

    async def fail_upload(request: httpx.Request) -> httpx.Response:
        if (
            request.method == "POST"
            and request.url.path == "/api/v1/homework/attachments"
        ):
            return httpx.Response(413, json={"detail": "file too large"})
        return await client.send(request)

    driver, http, api, _requests = await _make_driver(
        client, bot_service_key, forward=fail_upload
    )
    try:
        await driver.text("/start")
        await _open_assignment(driver, "Alice Learner")
        await driver.document("rejected.pdf", b"not stored")
        assert "20 MiB" in driver.session.last_message.text
        assert await _student_assignments(api, students[0]) == []
        assert (
            await db_session.scalar(
                select(func.count()).select_from(HomeworkAttachment)
            )
            == 0
        )
        await driver.click("Cancel")
    finally:
        await _close_driver(driver, http)
        app.dependency_overrides.pop(get_homework_settings, None)


async def test_unknown_upload_and_publish_retry_reuses_operation_keys(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    tmp_path: Path,
) -> None:
    app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
        STORAGE_PATH=tmp_path
    )
    _teacher, students = await _seed(db_session)
    lost_upload = False
    lost_publish = False
    upload_keys: list[str] = []
    publish_keys: list[str] = []

    async def lose_first_responses(request: httpx.Request) -> httpx.Response:
        nonlocal lost_upload, lost_publish
        response = await client.send(request)
        if (
            request.method == "POST"
            and request.url.path == "/api/v1/homework/attachments"
        ):
            upload_keys.append(request.headers["Idempotency-Key"])
            if not lost_upload:
                lost_upload = True
                raise httpx.ReadTimeout("Upload response lost")
        if (
            request.method == "POST"
            and request.url.path == "/api/v1/homework/assignments"
        ):
            publish_keys.append(request.headers["Idempotency-Key"])
            if not lost_publish:
                lost_publish = True
                raise httpx.ReadTimeout("Publish response lost")
        return response

    driver, http, api, _requests = await _make_driver(
        client, bot_service_key, forward=lose_first_responses
    )
    try:
        await driver.text("/start")
        await _open_assignment(driver, "Alice Learner")
        await driver.document("retry.pdf", b"same bytes")
        assert "Retry upload" in driver.session.last_message.text
        await driver.click("Retry upload: retry.pdf")
        await driver.click("Review")
        await driver.click("Publish assignment")
        driver.callback_data("Retry publish (same operation)")
        await driver.click("Retry publish (same operation)")

        assignments = await _student_assignments(api, students[0])
        assert len(assignments) == 1
        assert upload_keys[0] == upload_keys[1]
        assert publish_keys[0] == publish_keys[1]
        assert await db_session.scalar(select(func.count()).select_from(Homework)) == 1
        assert (
            await db_session.scalar(
                select(func.count()).select_from(HomeworkAttachment)
            )
            == 1
        )
    finally:
        await _close_driver(driver, http)
        app.dependency_overrides.pop(get_homework_settings, None)

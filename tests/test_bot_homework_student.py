from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.auth.utils import create_access_token
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.models import Homework, HomeworkAttachment, HomeworkSubmission
from backend.main import app
from telegram.api import MAX_HOMEWORK_ATTACHMENT_BYTES, BackendClient
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

TEACHER_TELEGRAM_ID = 5_710_000_001
STUDENT_TELEGRAM_ID = TEACHER_TELEGRAM_ID + 1
Forward = Callable[[httpx.Request], Awaitable[httpx.Response]]


async def _seed_pair(session: AsyncSession) -> tuple[User, User]:
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
    session.add_all(
        [
            teacher,
            student,
            TeacherProfile(
                teacher_id=teacher.id,
                code="t_STUDY",
                work_start=None,
                work_end=None,
                working_days=[],
                lesson_length=60,
                between_lessons_break=5,
                max_consecutive_lessons=4,
                max_lessons_per_day=6,
            ),
            StudentProfile(student_id=student.id),
        ]
    )
    await session.commit()
    return teacher, student


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def _create_assignment(
    client: httpx.AsyncClient,
    teacher: User,
    student: User,
    *,
    text: str = "Read chapter 3",
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


async def _make_student_driver(
    client: httpx.AsyncClient,
    bot_service_key: str,
    *,
    forward: Forward | None = None,
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
    driver = BotDriver(build_dispatcher(api), user_id=STUDENT_TELEGRAM_ID)
    return driver, http, api, requests


async def _close_driver(driver: BotDriver, http: httpx.AsyncClient) -> None:
    await driver.close()
    await http.aclose()


def _button_with_prefix(driver: BotDriver, prefix: str) -> str:
    assert driver.session.last_message is not None
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    return next(
        button.text
        for row in markup.inline_keyboard
        for button in row
        if button.text.startswith(prefix)
    )


async def _open_assignment(driver: BotDriver, assignment_text: str) -> None:
    await driver.click("Homework")
    await driver.click(_button_with_prefix(driver, assignment_text))


async def test_student_downloads_material_and_finishes_then_resubmits(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    tmp_path: Path,
) -> None:
    app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
        STORAGE_PATH=tmp_path
    )
    teacher, student = await _seed_pair(db_session)
    assignment_id = await _create_assignment(
        client,
        teacher,
        student,
        material=("worksheet.pdf", b"assignment material bytes"),
    )
    driver, http, _api, _requests = await _make_student_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await driver.click("Homework")
        assert "Read chapter 3" in _button_with_prefix(driver, "Read chapter 3")
        await driver.click(_button_with_prefix(driver, "Read chapter 3"))
        assert "Incomplete" in driver.session.last_message.text
        assert "worksheet.pdf" in _button_with_prefix(driver, "Download worksheet.pdf")

        active_message_id = driver.session.last_message.message_id
        await driver.click(_button_with_prefix(driver, "Download worksheet.pdf"))
        assert driver.session.sent_documents == [
            (STUDENT_TELEGRAM_ID, "worksheet.pdf", b"assignment material bytes")
        ]
        assert driver.session.last_message.message_id == active_message_id
        assert "Sent worksheet.pdf" in driver.session.last_message.text

        await driver.click("Submit solution")
        await driver.text("My answer")
        await driver.document("solution.pdf", b"solution attachment bytes")
        await driver.click("Review")
        assert "My answer" in driver.session.last_message.text
        assert "solution.pdf" in driver.session.last_message.text
        finish_callback = driver.callback_data("Finish submission")
        await driver.click(data=finish_callback)
        assert "version 1" in driver.session.last_message.text
        assert "completed the assignment" in driver.session.last_message.text

        homework = await db_session.get(Homework, assignment_id)
        assert homework is not None
        assert homework.status == "COMPLETE"
        assert homework.completion_source == "STUDENT_SUBMISSION"
        submissions = list(
            await db_session.scalars(
                select(HomeworkSubmission)
                .where(HomeworkSubmission.homework_id == assignment_id)
                .order_by(HomeworkSubmission.version)
            )
        )
        assert [(item.version, item.text) for item in submissions] == [(1, "My answer")]
        solution_attachment = await db_session.scalar(
            select(HomeworkAttachment).where(
                HomeworkAttachment.submission_id == submissions[0].id
            )
        )
        assert solution_attachment is not None
        assert solution_attachment.file_name == "solution.pdf"
        assert (tmp_path / solution_attachment.storage_key).read_bytes() == (
            b"solution attachment bytes"
        )

        duplicate_callback = driver.callback_data("Back to homework")
        await driver.click(data=finish_callback)
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(HomeworkSubmission)
                .where(HomeworkSubmission.homework_id == assignment_id)
            )
            == 1
        )
        await driver.click("Submit another version")
        await driver.text("Updated answer")
        await driver.click("Review")
        await driver.click("Finish submission")
        assert "version 2" in driver.session.last_message.text
        assert "stays complete" in driver.session.last_message.text
        await driver.click(data=duplicate_callback)
        versions = list(
            await db_session.scalars(
                select(HomeworkSubmission.version)
                .where(HomeworkSubmission.homework_id == assignment_id)
                .order_by(HomeworkSubmission.version)
            )
        )
        assert versions == [1, 2]
    finally:
        await _close_driver(driver, http)
        app.dependency_overrides.pop(get_homework_settings, None)


async def test_unknown_submission_retries_same_operation_without_duplicate_version(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, student = await _seed_pair(db_session)
    assignment_id = await _create_assignment(client, teacher, student)
    submission_requests: list[bytes] = []
    lose_first_response = True

    async def lose_first_result(request: httpx.Request) -> httpx.Response:
        nonlocal lose_first_response
        response = await client.send(request)
        if request.method == "POST" and request.url.path.endswith("/submissions"):
            submission_requests.append(request.content)
            if lose_first_response:
                lose_first_response = False
                return httpx.Response(503, json={"detail": "response lost"})
        return response

    driver, http, _api, _requests = await _make_student_driver(
        client, bot_service_key, forward=lose_first_result
    )
    try:
        await driver.text("/start")
        await _open_assignment(driver, "Read chapter 3")
        await driver.click("Submit solution")
        await driver.text("My answer")
        await driver.click("Review")
        await driver.click("Finish submission")
        assert "outcome is unknown" in driver.session.last_message.text.lower()
        assert "Retry Finish" in _button_with_prefix(driver, "Retry Finish")
        await driver.click("Retry Finish (same operation)")
        assert "version 1" in driver.session.last_message.text
        assert len(submission_requests) == 2
        assert submission_requests[0] == submission_requests[1]
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(HomeworkSubmission)
                .where(HomeworkSubmission.homework_id == assignment_id)
            )
            == 1
        )
        homework = await db_session.get(Homework, assignment_id)
        assert homework is not None
        assert homework.completion_source == "STUDENT_SUBMISSION"
    finally:
        await _close_driver(driver, http)


async def test_stale_deleted_assignment_does_not_report_completion(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, student = await _seed_pair(db_session)
    assignment_id = await _create_assignment(client, teacher, student)
    driver, http, _api, _requests = await _make_student_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await driver.click("Homework")
        stale_selection = driver.callback_data(
            _button_with_prefix(driver, "Read chapter 3")
        )
        deleted = await client.delete(
            f"/api/v1/homework/assignments/{assignment_id}",
            headers=_auth(teacher),
        )
        assert deleted.status_code == 204
        await driver.click(data=stale_selection)
        assert "no longer available" in driver.session.last_message.text
        assert "version" not in driver.session.last_message.text.lower()
        assert all(
            not button.text.startswith("Read chapter 3")
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(HomeworkSubmission)
                .where(HomeworkSubmission.homework_id == assignment_id)
            )
            == 0
        )
    finally:
        await _close_driver(driver, http)


async def test_teacher_completed_assignment_blocks_student_submission(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, student = await _seed_pair(db_session)
    assignment_id = await _create_assignment(client, teacher, student)
    completed = await client.post(
        f"/api/v1/homework/assignments/{assignment_id}/complete",
        headers=_auth(teacher),
        json={},
    )
    assert completed.status_code == 200, completed.text

    driver, http, _api, _requests = await _make_student_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await _open_assignment(driver, "Read chapter 3")
        assert "Marked complete by your teacher" in driver.session.last_message.text
        assert all(
            "Submit" not in button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(HomeworkSubmission)
                .where(HomeworkSubmission.homework_id == assignment_id)
            )
            == 0
        )
    finally:
        await _close_driver(driver, http)


async def test_student_assignment_list_paginates_and_filters_status(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, student = await _seed_pair(db_session)
    assignment_ids = [
        await _create_assignment(
            client, teacher, student, text=f"Assignment {index:02}"
        )
        for index in range(21)
    ]
    completed = await client.post(
        f"/api/v1/homework/assignments/{assignment_ids[0]}/complete",
        headers=_auth(teacher),
        json={},
    )
    assert completed.status_code == 200, completed.text

    driver, http, _api, _requests = await _make_student_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await driver.click("Homework")
        assert "Page 1 of 2 (21 assignments)" in driver.session.last_message.text
        await driver.click("Next")
        assert "Page 2 of 2 (21 assignments)" in driver.session.last_message.text
        await driver.click("Complete")
        assert "Page 1 of 1 (1 assignments)" in driver.session.last_message.text
        assert "complete" in _button_with_prefix(driver, "Assignment").lower()
        await driver.click("Incomplete")
        assert "Page 1 of 1 (20 assignments)" in driver.session.last_message.text
    finally:
        await _close_driver(driver, http)


async def test_oversized_solution_is_rejected_before_telegram_download(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, student = await _seed_pair(db_session)
    assignment_id = await _create_assignment(client, teacher, student)
    driver, http, _api, _requests = await _make_student_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await _open_assignment(driver, "Read chapter 3")
        await driver.click("Submit solution")
        get_file_calls = sum(
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
            == get_file_calls
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(HomeworkSubmission)
                .where(HomeworkSubmission.homework_id == assignment_id)
            )
            == 0
        )
    finally:
        await _close_driver(driver, http)


async def test_cancelled_solution_cleans_staged_file_without_completing_assignment(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    tmp_path: Path,
) -> None:
    app.dependency_overrides[get_homework_settings] = lambda: HomeworkConfig(
        STORAGE_PATH=tmp_path
    )
    teacher, student = await _seed_pair(db_session)
    assignment_id = await _create_assignment(client, teacher, student)
    driver, http, _api, _requests = await _make_student_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await _open_assignment(driver, "Read chapter 3")
        await driver.click("Submit solution")
        await driver.document("draft.pdf", b"discard this solution")
        await driver.click("Cancel")
        assert "Read chapter 3" in driver.session.last_message.text
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(HomeworkAttachment)
                .where(HomeworkAttachment.homework_id == assignment_id)
            )
            == 0
        )
        assert not list(tmp_path.iterdir())
        homework = await db_session.get(Homework, assignment_id)
        assert homework is not None
        assert homework.status == "INCOMPLETE"
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(HomeworkSubmission)
                .where(HomeworkSubmission.homework_id == assignment_id)
            )
            == 0
        )
    finally:
        await _close_driver(driver, http)
        app.dependency_overrides.pop(get_homework_settings, None)


async def test_submission_rejected_if_teacher_completes_draft_assignment(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    teacher, student = await _seed_pair(db_session)
    assignment_id = await _create_assignment(client, teacher, student)
    driver, http, _api, _requests = await _make_student_driver(client, bot_service_key)
    try:
        await driver.text("/start")
        await _open_assignment(driver, "Read chapter 3")
        await driver.click("Submit solution")
        await driver.text("A draft answer")
        await driver.click("Review")
        completed = await client.post(
            f"/api/v1/homework/assignments/{assignment_id}/complete",
            headers=_auth(teacher),
            json={},
        )
        assert completed.status_code == 200, completed.text

        await driver.click("Finish submission")
        assert "A draft answer" in driver.session.last_message.text
        assert "version" not in driver.session.last_message.text.lower()
        assert "Finish submission" in _button_with_prefix(driver, "Finish submission")
        homework = await db_session.get(Homework, assignment_id)
        assert homework is not None
        assert homework.status == "COMPLETE"
        assert homework.completion_source == "TEACHER"
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(HomeworkSubmission)
                .where(HomeworkSubmission.homework_id == assignment_id)
            )
            == 0
        )
    finally:
        await _close_driver(driver, http)


async def test_student_homework_backend_failure_can_be_retried(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
) -> None:
    _teacher, _student = await _seed_pair(db_session)
    fail_list = True

    async def fail_once(request: httpx.Request) -> httpx.Response:
        nonlocal fail_list
        if (
            fail_list
            and request.method == "GET"
            and request.url.path == "/api/v1/homework/assignments"
        ):
            fail_list = False
            return httpx.Response(503, json={"detail": "temporarily unavailable"})
        return await client.send(request)

    driver, http, _api, _requests = await _make_student_driver(
        client, bot_service_key, forward=fail_once
    )
    try:
        await driver.text("/start")
        await driver.click("Homework")
        assert "Backend unavailable" in driver.session.last_message.text
        await driver.click("Retry")
        assert "Your homework" in driver.session.last_message.text
        assert "temporarily unavailable" not in driver.session.last_message.text
    finally:
        await _close_driver(driver, http)

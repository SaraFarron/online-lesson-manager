import json
from datetime import UTC, datetime, time
from itertools import count
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.events.models import Event, RecurrentEvent
from backend.main import app
from backend.schedule.router import get_schedule_clock
from telegram.api import BackendClient
from telegram.lesson_dialogs import submit_lesson
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

_TELEGRAM_IDS = count(5_200_000_000, 2)


async def _seed_accounts(
    session: AsyncSession,
) -> tuple[User, User]:
    teacher_telegram_id = next(_TELEGRAM_IDS)
    student_telegram_id = teacher_telegram_id + 1
    teacher = User(
        id=uuid4(),
        full_name="Teacher Owner",
        role="TEACHER",
        timezone="UTC",
        telegram_user_id=teacher_telegram_id,
        telegram_private_chat_id=teacher_telegram_id,
        is_active=True,
    )
    student = User(
        id=uuid4(),
        full_name="Alice Learner",
        role="STUDENT",
        timezone="America/Los_Angeles",
        teacher_id=teacher.id,
        telegram_user_id=student_telegram_id,
        telegram_private_chat_id=student_telegram_id,
        is_active=True,
    )
    session.add_all([teacher, student])
    session.add(
        TeacherProfile(
            teacher_id=teacher.id,
            code="t_LESS06",
            work_start=time(9),
            work_end=time(18),
            working_days=[1, 2, 3, 4, 5],
            lesson_length=60,
            between_lessons_break=5,
            max_consecutive_lessons=4,
            max_lessons_per_day=6,
        )
    )
    session.add(StudentProfile(student_id=student.id))
    await session.commit()
    return teacher, student


def _telegram_id(user: User) -> int:
    if user.telegram_user_id is None:
        raise AssertionError("Seeded account must have a Telegram user ID")
    return user.telegram_user_id


async def _make_bot(
    client: httpx.AsyncClient,
    bot_service_key: str,
    telegram_id: int,
    *,
    lose_write_response: bool = False,
    schedule_change_conflict: bool = False,
) -> tuple[BackendClient, BotDriver, httpx.AsyncClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    lost_response = False

    async def forward(request: httpx.Request) -> httpx.Response:
        nonlocal lost_response
        requests.append(request)
        if (
            schedule_change_conflict
            and request.method == "POST"
            and request.url.path
            in {
                "/api/v1/lessons/",
                "/api/v1/recurrent-lessons/",
            }
        ):
            return httpx.Response(
                409,
                json={"detail": "Schedule is being changed; retry this request."},
            )
        response = await client.send(request)
        if (
            lose_write_response
            and not lost_response
            and request.method == "POST"
            and request.url.path
            in {
                "/api/v1/lessons/",
                "/api/v1/recurrent-lessons/",
            }
        ):
            lost_response = True
            raise httpx.ReadTimeout("The create response was lost")
        return response

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(forward), base_url="http://test"
    )
    api = BackendClient(http, bot_service_key)
    driver = BotDriver(build_dispatcher(api), user_id=telegram_id)
    return api, driver, http, requests


async def _close_bot(driver: BotDriver, http: httpx.AsyncClient) -> None:
    await driver.close()
    await http.aclose()


async def test_teacher_books_student_and_omits_default_duration(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, student = await _seed_accounts(db_session)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_schedule_clock,
        lambda: datetime(2026, 10, 20, 8, tzinfo=UTC),
    )
    api, driver, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await driver.text("/start")
        await driver.click("Add lesson")
        assert driver.callback_data("Alice Learner")
        await driver.click("Alice Learner")
        await driver.click("One time")
        await driver.click("Use default (60 min)")
        await driver.text("20 10 2026")
        await driver.text("25:00")
        assert "Hour must be 0–23" in driver.session.last_message.text
        await driver.text("10:00")
        assert "Timezone: UTC" in driver.session.last_message.text
        assert "Automatic break after this lesson: 5 minutes" in (
            driver.session.last_message.text
        )
        assert "2026-10-20 11:00 (UTC+00:00) – 2026-10-20 11:05 (UTC+00:00)" in (
            driver.session.last_message.text
        )
        submit = driver.callback_data("Create lesson")
        await driver.click(data=submit)

        events = list((await db_session.scalars(select(Event))).all())
        assert len(events) == 1
        assert events[0].user_id == student.id
        assert (events[0].end - events[0].start).total_seconds() == 3600
        create_requests = [
            request
            for request in requests
            if request.method == "POST" and request.url.path == "/api/v1/lessons/"
        ]
        assert len(create_requests) == 1
        body = json.loads(create_requests[0].content)
        assert set(body) == {"start", "user_id", "event_type"}
        assert body["user_id"] == str(student.id)
        assert "Alice Learner" in driver.session.last_message.text
        assert "Tuesday, October 20, 2026" in driver.session.last_message.text
        assert "Automatic break (read-only)" in driver.session.last_message.text
    finally:
        await _close_bot(driver, http)


async def test_teacher_creates_custom_weekly_personal_lesson(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_schedule_clock,
        lambda: datetime(2026, 10, 20, 8, tzinfo=UTC),
    )
    _, driver, http, _ = await _make_bot(client, bot_service_key, _telegram_id(teacher))
    try:
        await driver.text("/start")
        await driver.click("Add lesson")
        await driver.click("Personal lesson")
        await driver.click("Weekly")
        await driver.click("Custom duration")
        await driver.text("45")
        await driver.text("20/10/2026")
        await driver.text("10:00")
        await driver.text("27/10/2026")
        assert "Weekly through 2026-10-27 inclusive" in (
            driver.session.last_message.text
        )
        assert "Custom duration applies only to this lesson or series" in (
            driver.session.last_message.text
        )
        assert "Automatic break after each lesson: 5 minutes" in (
            driver.session.last_message.text
        )
        assert "2026-10-20 10:45 (UTC+00:00) – 2026-10-20 10:50 (UTC+00:00)" in (
            driver.session.last_message.text
        )
        await driver.click("Create lesson")

        series = list((await db_session.scalars(select(RecurrentEvent))).all())
        assert len(series) == 1
        assert series[0].user_id == teacher.id
        assert series[0].interval == 7
        assert (series[0].end - series[0].start).total_seconds() == 45 * 60
        assert series[0].interval_end == datetime(2026, 10, 28)
        assert "Personal lesson" in driver.session.last_message.text
        assert "Tuesday, October 20, 2026" in driver.session.last_message.text
    finally:
        await _close_bot(driver, http)


async def test_student_books_self_weekly_without_duration_or_subject_forgery(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, student = await _seed_accounts(db_session)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_schedule_clock,
        lambda: datetime(2026, 10, 20, 8, tzinfo=UTC),
    )
    api, driver, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(student)
    )
    try:
        await driver.text("/start")
        await driver.click("Add lesson")
        await driver.click("Weekly")
        await driver.text("20/10/2026")
        await driver.text("10:00")
        await driver.text("20/10/2026")
        assert "Automatic break after each lesson: 5 minutes" in (
            driver.session.last_message.text
        )
        assert "2026-10-20 11:00 (UTC-07:00) – 2026-10-20 11:05 (UTC-07:00)" in (
            driver.session.last_message.text
        )
        await driver.click("Create lesson")

        series = list((await db_session.scalars(select(RecurrentEvent))).all())
        assert len(series) == 1
        assert series[0].teacher_id == teacher.id
        assert series[0].user_id == student.id
        assert (series[0].end - series[0].start).total_seconds() == 3600
        assert series[0].interval_end == datetime(2026, 10, 21, 7)
        create_requests = [
            request
            for request in requests
            if request.method == "POST"
            and request.url.path == "/api/v1/recurrent-lessons/"
        ]
        assert len(create_requests) == 1
        body = json.loads(create_requests[0].content)
        assert "end" not in body
        assert body["user_id"] == str(student.id)
        assert body["interval"] == 7
        assert "duration" not in body
        assert "10:00" in driver.session.last_message.text

        headers = {"Authorization": f"Bearer {api.tokens[_telegram_id(student)]}"}
        duration_forgery = await client.post(
            "/api/v1/lessons/",
            headers=headers,
            json={
                "start": "2026-10-22T10:00:00+00:00",
                "end": "2026-10-22T11:30:00+00:00",
                "user_id": str(student.id),
                "event_type": "LESSON",
            },
        )
        assert duration_forgery.status_code == 422
        subject_forgery = await client.post(
            "/api/v1/lessons/",
            headers=headers,
            json={
                "start": "2026-10-22T10:00:00+00:00",
                "end": "2026-10-22T11:00:00+00:00",
                "user_id": str(teacher.id),
                "event_type": "LESSON",
            },
        )
        assert subject_forgery.status_code == 403
    finally:
        await _close_bot(driver, http)


async def test_policy_rejection_keeps_review_editable(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    _, driver, http, _ = await _make_bot(client, bot_service_key, _telegram_id(teacher))
    try:
        await driver.text("/start")
        await driver.click("Add lesson")
        await driver.click("Alice Learner")
        await driver.click("One time")
        await driver.click("Use default (60 min)")
        await driver.text("25/10/2026")
        await driver.text("10:00")
        await driver.click("Create lesson")

        assert "not one of the teacher" in (driver.session.last_message.text)
        await driver.click("Change date")
        assert "Enter the lesson start date" in driver.session.last_message.text
        assert not list((await db_session.scalars(select(Event))).all())
    finally:
        await _close_bot(driver, http)


async def test_lost_create_response_requires_schedule_reload_without_retry(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_schedule_clock,
        lambda: datetime(2026, 10, 20, 8, tzinfo=UTC),
    )
    _, driver, http, requests = await _make_bot(
        client,
        bot_service_key,
        _telegram_id(teacher),
        lose_write_response=True,
    )
    try:
        await driver.text("/start")
        await driver.click("Add lesson")
        await driver.click("Alice Learner")
        await driver.click("One time")
        await driver.click("Use default (60 min)")
        await driver.text("20/10/2026")
        await driver.text("12:00")
        await driver.click("Create lesson")

        assert "outcome is unknown" in driver.session.last_message.text
        assert driver.callback_data("Reload schedule")
        assert len(list((await db_session.scalars(select(Event))).all())) == 1
        await driver.click("Reload schedule")

        create_requests = [
            request
            for request in requests
            if request.method == "POST" and request.url.path == "/api/v1/lessons/"
        ]
        assert len(create_requests) == 1
        assert "Alice Learner" in driver.session.last_message.text
        assert "Tuesday, October 20, 2026" in driver.session.last_message.text
    finally:
        await _close_bot(driver, http)


async def test_concurrent_schedule_change_shows_error_and_requires_reload(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_schedule_clock,
        lambda: datetime(2026, 10, 20, 8, tzinfo=UTC),
    )
    _, driver, http, requests = await _make_bot(
        client,
        bot_service_key,
        _telegram_id(teacher),
        schedule_change_conflict=True,
    )
    try:
        await driver.text("/start")
        await driver.click("Add lesson")
        await driver.click("Alice Learner")
        await driver.click("One time")
        await driver.click("Use default (60 min)")
        await driver.text("20/10/2026")
        await driver.text("12:00")
        await driver.click("Create lesson")

        assert "schedule is being changed" in driver.session.last_message.text
        assert driver.callback_data("Reload schedule")
        assert not list((await db_session.scalars(select(Event))).all())
        assert (
            sum(
                request.method == "POST" and request.url.path == "/api/v1/lessons/"
                for request in requests
            )
            == 1
        )
        await driver.click("Reload schedule")
        assert "This week's schedule" in driver.session.last_message.text
    finally:
        await _close_bot(driver, http)


async def test_automatic_break_conflict_is_actionable_in_booking_review(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, student = await _seed_accounts(db_session)
    db_session.add(
        Event(
            teacher_id=teacher.id,
            user_id=student.id,
            event_type="LESSON",
            start=datetime(2026, 10, 20, 10),
            end=datetime(2026, 10, 20, 11),
            post_break_minutes=5,
        )
    )
    await db_session.commit()
    monkeypatch.setitem(
        app.dependency_overrides,
        get_schedule_clock,
        lambda: datetime(2026, 10, 20, 8, tzinfo=UTC),
    )
    _, driver, http, _ = await _make_bot(client, bot_service_key, _telegram_id(teacher))
    try:
        await driver.text("/start")
        await driver.click("Add lesson")
        await driver.click("Alice Learner")
        await driver.click("One time")
        await driver.click("Use default (60 min)")
        await driver.text("20/10/2026")
        await driver.text("11:02")
        await driver.click("Create lesson")

        assert (
            "That time overlaps an automatic break reserved after a lesson."
            in driver.session.last_message.text
        )
        assert "Choose another time." in driver.session.last_message.text
        assert len(list((await db_session.scalars(select(Event))).all())) == 1
    finally:
        await _close_bot(driver, http)


async def test_new_automatic_break_conflict_is_not_misreported_as_existing_break(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    db_session.add(
        Event(
            teacher_id=teacher.id,
            user_id=teacher.id,
            event_type="BREAK",
            start=datetime(2026, 10, 20, 11, 2),
            end=datetime(2026, 10, 20, 11, 4),
        )
    )
    await db_session.commit()
    monkeypatch.setitem(
        app.dependency_overrides,
        get_schedule_clock,
        lambda: datetime(2026, 10, 20, 8, tzinfo=UTC),
    )
    _, driver, http, _ = await _make_bot(client, bot_service_key, _telegram_id(teacher))
    try:
        await driver.text("/start")
        await driver.click("Add lesson")
        await driver.click("Alice Learner")
        await driver.click("One time")
        await driver.click("Use default (60 min)")
        await driver.text("20/10/2026")
        await driver.text("10:00")
        await driver.click("Create lesson")

        assert (
            "The automatic break after this lesson would conflict with another "
            "scheduled event."
        ) in driver.session.last_message.text
        assert "Choose a different time or duration." in (
            driver.session.last_message.text
        )
        assert len(list((await db_session.scalars(select(Event))).all())) == 1
    finally:
        await _close_bot(driver, http)


@pytest.mark.parametrize("state", [{"submitted": True}, {"submitting": True}])
async def test_duplicate_submit_callback_is_ignored(state: dict[str, bool]) -> None:
    manager = SimpleNamespace(dialog_data=state)

    await submit_lesson(None, None, manager)

    assert manager.dialog_data == state

from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import (
    StudentProfile,
    StudentTeacherRelationship,
    TeacherProfile,
    User,
)
from backend.events.models import Event
from backend.main import app
from backend.schedule.router import get_schedule_clock
from telegram.api import (
    BackendClient,
    BackendError,
    CircuitBreaker,
    ScheduleSnapshotCache,
    StudentRemovalConfirmRequest,
)
from telegram.runtime import build_dispatcher
from telegram.shared.inputs import format_compact_schedule_date
from tests.bot_support import BotDriver

TEACHER_TELEGRAM_ID = 5_810_000_001
STUDENT_TELEGRAM_ID = TEACHER_TELEGRAM_ID + 1
SCHEDULE_NOW = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
SCHEDULE_DATE = SCHEDULE_NOW.date()


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class _BackendTransport:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.offline = False
        self.paths: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if self.offline:
            raise httpx.ConnectError("simulated backend outage", request=request)
        return await self.client.send(request)


async def _seed_teacher_student(
    session: AsyncSession,
) -> tuple[User, User, StudentTeacherRelationship]:
    teacher = User(
        id=uuid4(),
        email=None,
        full_name="E2E Teacher",
        role="TEACHER",
        timezone="UTC",
        telegram_user_id=TEACHER_TELEGRAM_ID,
        telegram_private_chat_id=TEACHER_TELEGRAM_ID,
        is_active=True,
    )
    student = User(
        id=uuid4(),
        email=None,
        full_name="E2E Learner",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
        telegram_user_id=STUDENT_TELEGRAM_ID,
        telegram_private_chat_id=STUDENT_TELEGRAM_ID,
        is_active=True,
    )
    relationship = StudentTeacherRelationship(
        teacher_id=teacher.id,
        student_id=student.id,
        linked_at=SCHEDULE_NOW - timedelta(days=30),
    )
    session.add_all(
        [
            teacher,
            student,
            TeacherProfile(
                teacher_id=teacher.id,
                code="t_E2E001",
                work_start=None,
                work_end=None,
                working_days=[],
            ),
            StudentProfile(student_id=student.id),
            relationship,
            Event(
                teacher_id=teacher.id,
                user_id=student.id,
                event_type="LESSON",
                start=datetime.combine(SCHEDULE_DATE, time(9)),
                end=datetime.combine(SCHEDULE_DATE, time(10)),
            ),
        ]
    )
    await session.commit()
    return teacher, student, relationship


async def _make_bot(
    service_key: str,
    telegram_id: int,
    *,
    transport: _BackendTransport,
    clock: _Clock,
) -> tuple[BotDriver, httpx.AsyncClient, BackendClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    )
    api = BackendClient(
        http,
        service_key,
        breaker=CircuitBreaker(threshold=1, recovery=30, clock=clock),
        schedule_cache=ScheduleSnapshotCache(clock=clock),
    )
    driver = BotDriver(build_dispatcher(api), user_id=telegram_id)
    return driver, http, api


async def _close(
    drivers: tuple[BotDriver, ...], clients: tuple[httpx.AsyncClient, ...]
) -> None:
    for driver in drivers:
        await driver.close()
    for client in clients:
        await client.aclose()


def _button_labels(driver: BotDriver) -> list[str]:
    assert driver.session.last_message is not None
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    return [button.text for row in markup.inline_keyboard for button in row]


@pytest.mark.asyncio
async def test_dispatcher_keeps_offline_schedule_scoped_and_invalidates_removed_student(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        app.dependency_overrides, get_schedule_clock, lambda: SCHEDULE_NOW
    )
    _teacher, student, relationship = await _seed_teacher_student(db_session)
    clock = _Clock()
    transport = _BackendTransport(client)
    teacher_driver, teacher_http, teacher_api = await _make_bot(
        bot_service_key,
        TEACHER_TELEGRAM_ID,
        transport=transport,
        clock=clock,
    )
    student_driver, student_http, student_api = await _make_bot(
        bot_service_key,
        STUDENT_TELEGRAM_ID,
        transport=transport,
        clock=clock,
    )
    try:
        await teacher_driver.text("/start")
        teacher_stale_callback = teacher_driver.callback_data("Add lesson")
        await teacher_driver.click("This week")
        live_teacher = teacher_driver.session.last_message.text
        assert "E2E Learner" in live_teacher

        await student_driver.text("/start")
        student_stale_callback = student_driver.callback_data("Homework")
        await student_driver.click("This week")
        live_student = student_driver.session.last_message.text
        assert "E2E Learner" not in live_student
        assert "E2E Teacher" not in live_student

        await teacher_driver.text("/start")
        await student_driver.text("/start")
        transport.offline = True
        transport.paths.clear()

        await teacher_driver.text("/start")
        assert "previously cached" in teacher_driver.session.last_message.text.lower()
        assert set(_button_labels(teacher_driver)) == {"Today", "This week"}
        await teacher_driver.click("This week")
        cached_teacher = teacher_driver.session.last_message.text
        assert "E2E Learner" in cached_teacher
        assert "OFFLINE" in cached_teacher
        assert "as of" in cached_teacher.lower()
        assert "12:00" in cached_teacher
        assert "NOW" not in cached_teacher
        week_start = SCHEDULE_DATE - timedelta(days=SCHEDULE_DATE.weekday())
        expected_week = format_compact_schedule_date(
            week_start, reference_date=SCHEDULE_DATE
        )
        assert f"Week starting {expected_week}" in cached_teacher

        await student_driver.text("/start")
        assert "previously cached" in student_driver.session.last_message.text.lower()
        assert set(_button_labels(student_driver)) == {"Today", "This week"}
        await student_driver.click("This week")
        cached_student = student_driver.session.last_message.text
        assert "E2E Learner" not in cached_student
        assert "E2E Teacher" not in cached_student
        assert "OFFLINE" in cached_student
        assert "as of" in cached_student.lower()
        assert "12:00" in cached_student
        assert f"Week starting {expected_week}" in cached_student

        paths_before_stale_callbacks = list(transport.paths)
        await teacher_driver.click(data=teacher_stale_callback)
        await student_driver.click(data=student_stale_callback)
        assert transport.paths == paths_before_stale_callbacks

        await teacher_driver.click("Back to home")
        clock.value += 60 * 60
        await teacher_driver.click("Today")
        assert "no eligible cached" in teacher_driver.session.last_message.text.lower()

        transport.offline = False
        clock.value += 31
        preview = await teacher_api.preview_teacher_student_removal(
            TEACHER_TELEGRAM_ID,
            TEACHER_TELEGRAM_ID,
            student.id,
        )
        removal = await teacher_api.confirm_teacher_student_removal(
            TEACHER_TELEGRAM_ID,
            TEACHER_TELEGRAM_ID,
            student.id,
            StudentRemovalConfirmRequest(
                preview_token=preview.confirmation_token,
                operation_key=uuid4(),
            ),
        )
        assert removal.student_id == student.id
        await db_session.refresh(student)
        assert student.teacher_id is None

        await student_driver.text("/start")
        assert student_api.offline_schedule_identity(STUDENT_TELEGRAM_ID) is None
        transport.offline = True
        await student_driver.text("/start")
        assert "unavailable" in student_driver.session.last_message.text.lower()
        assert "Today" not in student_driver.session.last_message.text
    finally:
        await _close(
            (teacher_driver, student_driver),
            (teacher_http, student_http),
        )

    await db_session.refresh(relationship)
    assert relationship.removed_at is not None
    assert teacher_api.offline_schedule_identity(TEACHER_TELEGRAM_ID) is not None


@pytest.mark.asyncio
async def test_live_account_revalidation_replaces_timezone_and_role_identity(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        app.dependency_overrides, get_schedule_clock, lambda: SCHEDULE_NOW
    )
    teacher, _student, _relationship = await _seed_teacher_student(db_session)
    clock = _Clock()
    transport = _BackendTransport(client)
    driver, http, api = await _make_bot(
        bot_service_key,
        TEACHER_TELEGRAM_ID,
        transport=transport,
        clock=clock,
    )

    try:
        await driver.text("/start")
        await driver.click("Today")
        old_identity = api.offline_schedule_identity(TEACHER_TELEGRAM_ID)
        assert old_identity is not None
        assert old_identity.timezone == "UTC"

        teacher.timezone = "Asia/Bangkok"
        await db_session.commit()
        await driver.text("/start")
        timezone_result = await api.read_schedule_view(
            TEACHER_TELEGRAM_ID,
            TEACHER_TELEGRAM_ID,
            "day",
            requested_date=SCHEDULE_DATE,
        )
        assert timezone_result.source == "live"
        assert timezone_result.response.timezone == "Asia/Bangkok"
        new_identity = api.offline_schedule_identity(TEACHER_TELEGRAM_ID)
        assert new_identity is not None
        assert new_identity.timezone == "Asia/Bangkok"
        assert new_identity != old_identity
        assert api.schedule_cache.get(old_identity, "day", SCHEDULE_DATE) is None

        teacher.role = "ADMIN"
        await db_session.commit()
        with pytest.raises(BackendError) as error:
            await api.read_schedule_view(
                TEACHER_TELEGRAM_ID,
                TEACHER_TELEGRAM_ID,
                "day",
                requested_date=SCHEDULE_DATE,
            )
        assert error.value.status == 403
        assert api.offline_schedule_identity(TEACHER_TELEGRAM_ID) is None

        await driver.text("/start")
        assert "ADMIN" in driver.session.last_message.text
        transport.offline = True
        await driver.text("/start")
        assert "unavailable" in driver.session.last_message.text.lower()
        assert "Today" not in driver.session.last_message.text
    finally:
        await _close((driver,), (http,))

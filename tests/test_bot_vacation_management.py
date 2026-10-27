from datetime import UTC, date, datetime, time, timedelta
from itertools import count
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from telegram.api import (
    BackendClient,
    BackendError,
    EventCreatePayload,
    EventWriteResult,
)
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

_TELEGRAM_IDS = count(5_700_000_000, 3)


async def _seed_accounts(
    session: AsyncSession, *, timezone: str = "Asia/Bangkok"
) -> tuple[User, User, User]:
    teacher_telegram_id = next(_TELEGRAM_IDS)
    teacher = User(
        id=uuid4(),
        full_name="Teacher Owner",
        role="TEACHER",
        timezone=timezone,
        telegram_user_id=teacher_telegram_id,
        telegram_private_chat_id=teacher_telegram_id,
        is_active=True,
    )
    student = User(
        id=uuid4(),
        full_name="Alice Learner",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
        telegram_user_id=teacher_telegram_id + 1,
        telegram_private_chat_id=teacher_telegram_id + 1,
        is_active=True,
    )
    other_student = User(
        id=uuid4(),
        full_name="Bob Learner",
        role="STUDENT",
        timezone="UTC",
        teacher_id=teacher.id,
        telegram_user_id=teacher_telegram_id + 2,
        telegram_private_chat_id=teacher_telegram_id + 2,
        is_active=True,
    )
    session.add_all(
        [
            teacher,
            student,
            other_student,
            TeacherProfile(
                teacher_id=teacher.id,
                code=f"t_{teacher_telegram_id % 1_000_000:06d}",
                work_start=time(9),
                work_end=time(18),
                working_days=[1, 2, 3, 4, 5],
                lesson_length=60,
                between_lessons_break=5,
                max_consecutive_lessons=4,
                max_lessons_per_day=6,
            ),
            StudentProfile(student_id=student.id),
            StudentProfile(student_id=other_student.id),
        ]
    )
    await session.commit()
    return teacher, student, other_student


def _lesson(
    teacher: User, student: User, start: datetime, *, end: datetime | None = None
) -> Event:
    return Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=start,
        end=end or start + timedelta(hours=1),
    )


async def _make_bot(
    client: httpx.AsyncClient,
    bot_service_key: str,
    telegram_id: int,
    *,
    lose_create_response: bool = False,
) -> tuple[BotDriver, BackendClient, httpx.AsyncClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    lost_response = False

    async def forward(request: httpx.Request) -> httpx.Response:
        nonlocal lost_response
        requests.append(request)
        response = await client.send(request)
        if (
            lose_create_response
            and not lost_response
            and request.method == "POST"
            and request.url.path == "/api/v1/vacations/"
        ):
            lost_response = True
            raise httpx.ReadTimeout("The vacation creation response was lost")
        return response

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(forward), base_url="http://test"
    )
    api = BackendClient(http, bot_service_key)
    driver = BotDriver(build_dispatcher(api), user_id=telegram_id)
    return driver, api, http, requests


async def _close_bot(driver: BotDriver, http: httpx.AsyncClient) -> None:
    await driver.close()
    await http.aclose()


def _telegram_id(user: User) -> int:
    if user.telegram_user_id is None:
        raise AssertionError("Seeded account must have a Telegram ID")
    return user.telegram_user_id


async def _open_vacations(driver: BotDriver) -> None:
    await driver.text("/start")
    await driver.click("Vacations")


async def _create_vacation(
    driver: BotDriver,
    *,
    subject: str | None,
    start_date: str,
    start_time: str,
    end_date: str,
) -> None:
    await driver.click("Add vacation")
    if subject is not None:
        await driver.click(subject)
    screen_message_id = driver.session.last_message.message_id
    start_message_id = driver.update_id + 1
    await driver.text(start_date)
    assert driver.deleted_message_ids()[-1] == start_message_id
    assert driver.session.last_message.message_id == screen_message_id
    await driver.text(start_time)
    end_message_id = driver.update_id + 1
    await driver.text(end_date)
    assert driver.deleted_message_ids()[-1] == end_message_id
    await driver.click("Save vacation")


def _vacation_button(driver: BotDriver, subject: str) -> str:
    assert driver.session.last_message is not None
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    for row in markup.inline_keyboard:
        for button in row:
            if subject in button.text and button.callback_data is not None:
                return button.text
    raise AssertionError(f"No vacation for {subject!r} on the current page")


def _schedule_items(schedule) -> list:
    return [item for day in schedule.days for item in day.items]


async def test_vacation_rejects_end_date_before_start_without_deleting_it(
    client, db_session, bot_service_key
) -> None:
    teacher, _student, _other_student = await _seed_accounts(db_session)
    driver, _api, http, _requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_vacations(driver)
        await driver.click("Add vacation")
        await driver.click("Personal vacation")

        screen_message_id = driver.session.last_message.message_id
        start_message_id = driver.update_id + 1
        await driver.text("05/11/2030")
        assert driver.deleted_message_ids() == [start_message_id]
        assert driver.session.last_message.message_id == screen_message_id
        await driver.text("10:00")

        rejected_message_id = driver.update_id + 1
        await driver.text("04/11/2030")
        assert "cannot be before its start date" in driver.session.last_message.text
        assert driver.deleted_message_ids() == [start_message_id]
        assert rejected_message_id not in driver.deleted_message_ids()
    finally:
        await _close_bot(driver, http)


async def test_teacher_creates_edits_and_deletes_student_vacation(
    client, db_session, bot_service_key
) -> None:
    teacher, student, other_student = await _seed_accounts(db_session)
    hidden_lesson = _lesson(teacher, student, datetime(2030, 11, 4, 3))
    boundary_lesson = _lesson(
        teacher,
        student,
        datetime(2030, 11, 6, 17),
        end=datetime(2030, 11, 6, 18),
    )
    other_lesson = _lesson(teacher, other_student, datetime(2030, 11, 4, 3))
    cancelled_series = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=datetime(2030, 11, 4, 3),
        end=datetime(2030, 11, 4, 4),
        interval=7,
        interval_end=datetime(2030, 12, 1, 3),
        recurrence_mode="UTC_STEP",
    )
    db_session.add_all([hidden_lesson, boundary_lesson, other_lesson, cancelled_series])
    await db_session.flush()
    cancellation = RecurrentCancels(
        recurrent_event_id=cancelled_series.id,
        start=cancelled_series.start,
        end=cancelled_series.end,
    )
    db_session.add(cancellation)
    await db_session.commit()

    driver, api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    student_bot, _, student_http, _ = await _make_bot(
        client, bot_service_key, _telegram_id(student)
    )
    other_bot, _, other_http, _ = await _make_bot(
        client, bot_service_key, _telegram_id(other_student)
    )
    try:
        await _open_vacations(driver)
        await driver.click("Vacation list")
        assert "No vacations." in driver.session.last_message.text
        assert "Vacations " not in driver.session.last_message.text
        await _create_vacation(
            driver,
            subject="Alice Learner",
            start_date="04/11/2030",
            start_time="10:00",
            end_date="04/11/2030",
        )
        assert "Vacation created." in driver.session.last_message.text
        assert "Vacations " not in driver.session.last_message.text
        assert "Alice Learner" in _vacation_button(driver, "Alice Learner")
        vacation = (
            await db_session.execute(
                select(Event).where(Event.event_type == EventType.VACATION.value)
            )
        ).scalar_one()
        assert vacation.user_id == student.id
        assert vacation.start == datetime(2030, 11, 4, 3)
        assert vacation.end == datetime(2030, 11, 4, 17)

        await student_bot.text("/start")
        await other_bot.text("/start")
        student_schedule = await api.read_schedule_day(
            _telegram_id(student), _telegram_id(student), date(2030, 11, 4)
        )
        other_schedule = await api.read_schedule_day(
            _telegram_id(other_student),
            _telegram_id(other_student),
            date(2030, 11, 4),
        )
        assert hidden_lesson.id not in {
            item.source_id for item in _schedule_items(student_schedule)
        }
        assert other_lesson.id in {
            item.source_id for item in _schedule_items(other_schedule)
        }

        # The management list remains available even when a vacation hides schedule events.
        listed = await api.read_management_page(
            _telegram_id(teacher), _telegram_id(teacher), "vacations", limit=20
        )
        assert [item.id for item in listed.items] == [vacation.id]
        await driver.click(_vacation_button(driver, "Alice Learner"))
        assert "Last vacation date (inclusive): November 4" in (
            driver.session.last_message.text
        )
        assert "2030-11-04" not in driver.session.last_message.text
        assert "(UTC" not in driver.session.last_message.text

        await driver.click("Edit vacation")
        await driver.click("Change last vacation date")
        await driver.text("06/11/2030")
        assert "November 6" in driver.session.last_message.text
        await driver.click("Save vacation")
        assert "Vacation updated." in driver.session.last_message.text
        updated = await db_session.get(Event, vacation.id)
        assert updated is not None
        await db_session.refresh(updated)
        assert updated.user_id == student.id
        assert updated.start == datetime(2030, 11, 4, 3)
        assert updated.end == datetime(2030, 11, 6, 17)
        assert "Vacation updated." in driver.session.last_message.text
        update_request = next(
            request
            for request in requests
            if request.method == "PUT"
            and request.url.path == f"/api/v1/vacations/{vacation.id}"
        )
        assert update_request.headers.get("if-match")

        end_boundary = await api.read_schedule_day(
            _telegram_id(student), _telegram_id(student), date(2030, 11, 6)
        )
        assert boundary_lesson.id in {
            item.source_id for item in _schedule_items(end_boundary)
        }

        await driver.click("Back to list")
        await driver.click(_vacation_button(driver, "Alice Learner"))
        await driver.click("Delete vacation")
        assert await db_session.get(Event, vacation.id) is not None
        assert "will not be restored" in driver.session.last_message.text
        await driver.click("Confirm delete")
        deleted_id = await db_session.scalar(
            select(Event.id).where(Event.id == vacation.id)
        )
        assert deleted_id is None
        assert "Vacation deleted." in driver.session.last_message.text

        restored_schedule = await api.read_schedule_day(
            _telegram_id(student), _telegram_id(student), date(2030, 11, 4)
        )
        assert hidden_lesson.id in {
            item.source_id for item in _schedule_items(restored_schedule)
        }
        assert await db_session.get(Event, hidden_lesson.id) is not None
        assert await db_session.get(Event, boundary_lesson.id) is not None
        assert await db_session.get(Event, other_lesson.id) is not None
        persisted_cancellation = await db_session.get(RecurrentCancels, cancellation.id)
        assert persisted_cancellation is not None
        assert persisted_cancellation.start == cancelled_series.start
        assert await db_session.get(RecurrentEvent, cancelled_series.id) is not None
        _assert_same_message(driver)
    finally:
        await _close_bot(driver, http)
        await _close_bot(student_bot, student_http)
        await _close_bot(other_bot, other_http)


async def test_teacher_personal_vacation_hides_the_roster_and_students_cannot_manage_it(
    client, db_session, bot_service_key
) -> None:
    teacher, student, other_student = await _seed_accounts(db_session)
    first_lesson = _lesson(teacher, student, datetime(2030, 11, 4, 3))
    second_lesson = _lesson(teacher, other_student, datetime(2030, 11, 4, 3))
    db_session.add_all([first_lesson, second_lesson])
    await db_session.commit()

    driver, api, http, _ = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    student_driver, student_api, student_http, _ = await _make_bot(
        client, bot_service_key, _telegram_id(student)
    )
    try:
        await _open_vacations(driver)
        await _create_vacation(
            driver,
            subject="Personal vacation",
            start_date="04/11/2030",
            start_time="10:00",
            end_date="04/11/2030",
        )
        teacher_vacation = (
            await db_session.execute(
                select(Event).where(Event.event_type == EventType.VACATION.value)
            )
        ).scalar_one()
        assert teacher_vacation.user_id == teacher.id

        await student_driver.text("/start")
        try:
            await student_api.read_management_detail(
                _telegram_id(student),
                _telegram_id(student),
                "vacations",
                teacher_vacation.id,
            )
        except BackendError as exc:
            assert exc.status == 403
        else:
            raise AssertionError("Student read a teacher-owned vacation")

        with pytest.raises(BackendError) as forged_create:
            await student_api.write_management_event(
                "POST",
                "vacations",
                _telegram_id(student),
                _telegram_id(student),
                EventCreatePayload(
                    start=datetime(2030, 11, 4, 3, tzinfo=UTC),
                    end=datetime(2030, 11, 5, 3, tzinfo=UTC),
                    user_id=other_student.id,
                    event_type="VACATION",
                ),
                EventWriteResult,
            )
        assert forged_create.value.status == 403

        teacher_schedule = await api.read_schedule_day(
            _telegram_id(teacher), _telegram_id(teacher), date(2030, 11, 4)
        )
        student_schedule = await api.read_schedule_day(
            _telegram_id(student), _telegram_id(student), date(2030, 11, 4)
        )
        assert _schedule_items(teacher_schedule) == []
        assert _schedule_items(student_schedule) == []
        _assert_same_message(driver)
    finally:
        await _close_bot(driver, http)
        await _close_bot(student_driver, student_http)


async def test_student_creates_own_vacation_and_unknown_create_is_not_replayed(
    client, db_session, bot_service_key
) -> None:
    teacher, student, other_student = await _seed_accounts(db_session)
    driver, api, http, requests = await _make_bot(
        client,
        bot_service_key,
        _telegram_id(student),
        lose_create_response=True,
    )
    try:
        await _open_vacations(driver)
        markup = driver.session.last_message.reply_markup
        assert markup is not None
        buttons = [button.text for row in markup.inline_keyboard for button in row]
        assert "Add vacation" in buttons
        await _create_vacation(
            driver,
            subject=None,
            start_date="04/11/2030",
            start_time="09:30",
            end_date="05/11/2030",
        )
        assert "result is unknown" in driver.session.last_message.text
        vacation = (
            await db_session.execute(
                select(Event).where(Event.event_type == EventType.VACATION.value)
            )
        ).scalar_one()
        assert vacation.user_id == student.id
        assert vacation.start == datetime(2030, 11, 4, 9, 30)
        assert vacation.end == datetime(2030, 11, 6)
        assert (
            sum(
                request.method == "POST" and request.url.path == "/api/v1/vacations/"
                for request in requests
            )
            == 1
        )
        page = await api.read_management_page(
            _telegram_id(student), _telegram_id(student), "vacations", limit=20
        )
        assert [item.id for item in page.items] == [vacation.id]

        # Students cannot target another student's vacation, even with a forged ID.
        other_vacation = Event(
            teacher_id=teacher.id,
            user_id=other_student.id,
            event_type=EventType.VACATION.value,
            start=datetime(2030, 12, 1),
            end=datetime(2030, 12, 2),
        )
        db_session.add(other_vacation)
        await db_session.commit()
        own_list = await api.read_management_page(
            _telegram_id(student), _telegram_id(student), "vacations", limit=20
        )
        assert [item.id for item in own_list.items] == [vacation.id]
        with pytest.raises(BackendError) as cross_student:
            await api.read_management_detail(
                _telegram_id(student),
                _telegram_id(student),
                "vacations",
                other_vacation.id,
            )
        assert cross_student.value.status == 403
        _assert_same_message(driver)
    finally:
        await _close_bot(driver, http)


def _assert_same_message(driver: BotDriver) -> None:
    calls = driver.session.calls
    sent = [call for call in calls if call.__api_method__ == "sendMessage"]
    edited = [call for call in calls if call.__api_method__ == "editMessageText"]
    assert len(sent) == 1
    assert edited
    assert driver.session.last_message is not None
    assert {call.model_dump().get("message_id") for call in edited} == {
        driver.session.last_message.message_id
    }

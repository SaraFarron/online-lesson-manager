import json
from datetime import UTC, date, datetime, time, timedelta
from itertools import count
from uuid import UUID, uuid4

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
    BackendUnavailable,
    EventCreatePayload,
    EventWriteResult,
)
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

_TELEGRAM_IDS = count(5_500_000_000, 2)


async def _seed_accounts(
    session: AsyncSession, *, timezone: str = "UTC"
) -> tuple[User, User]:
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
        timezone=timezone,
        teacher_id=teacher.id,
        telegram_user_id=teacher_telegram_id + 1,
        telegram_private_chat_id=teacher_telegram_id + 1,
        is_active=True,
    )
    session.add_all(
        [
            teacher,
            student,
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
        ]
    )
    await session.commit()
    return teacher, student


def _lesson(teacher: User, student: User, start: datetime) -> Event:
    return Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=start,
        end=start + timedelta(hours=1),
    )


def _weekly_lesson(
    teacher: User,
    student: User,
    start: datetime,
    *,
    interval_end: datetime,
) -> RecurrentEvent:
    return RecurrentEvent(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=start,
        end=start + timedelta(hours=1),
        interval=7,
        interval_end=interval_end,
        recurrence_mode="TEACHER_LOCAL",
        recurrence_timezone="UTC",
    )


def _break(
    teacher: User,
    start: datetime,
    end: datetime,
    *,
    interval: int | None = None,
    interval_end: datetime | None = None,
) -> Event | RecurrentEvent:
    if interval is None:
        return Event(
            teacher_id=teacher.id,
            user_id=teacher.id,
            event_type=EventType.BREAK.value,
            start=start,
            end=end,
        )
    if interval_end is None:
        raise ValueError("Recurring break requires an interval end")
    return RecurrentEvent(
        teacher_id=teacher.id,
        user_id=teacher.id,
        event_type=EventType.BREAK.value,
        start=start,
        end=end,
        interval=interval,
        interval_end=interval_end,
        recurrence_mode="TEACHER_LOCAL",
        recurrence_timezone="UTC",
    )


async def _make_bot(
    client: httpx.AsyncClient,
    bot_service_key: str,
    telegram_id: int,
    *,
    lose_create_response: bool = False,
    resolution_failure_status: int | None = None,
    stale_update_once: bool = False,
    delete_failure: str | None = None,
) -> tuple[BotDriver, BackendClient, httpx.AsyncClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    lost_response = False
    stale_response_sent = False

    async def forward(request: httpx.Request) -> httpx.Response:
        nonlocal lost_response, stale_response_sent
        requests.append(request)
        if (
            resolution_failure_status is not None
            and request.method == "POST"
            and request.url.path == "/api/v1/events/resolve-local-time"
        ):
            return httpx.Response(
                resolution_failure_status,
                json={"detail": "Resolution rejected for this test."},
                request=request,
            )
        if (
            stale_update_once
            and not stale_response_sent
            and request.method == "PUT"
            and request.url.path.startswith(
                ("/api/v1/breaks/", "/api/v1/recurrent-breaks/")
            )
        ):
            stale_response_sent = True
            return httpx.Response(
                409,
                json={
                    "detail": {
                        "code": "stale_event_revision",
                        "message": "stale test revision",
                    }
                },
                request=request,
            )
        if (
            delete_failure == "stale"
            and request.method == "DELETE"
            and request.url.path.startswith(
                ("/api/v1/breaks/", "/api/v1/recurrent-breaks/")
            )
        ):
            return httpx.Response(
                409,
                json={
                    "detail": {
                        "code": "stale_event_revision",
                        "message": "stale test revision",
                    }
                },
                request=request,
            )
        response = await client.send(request)
        if (
            delete_failure == "lost"
            and request.method == "DELETE"
            and request.url.path.startswith(
                ("/api/v1/breaks/", "/api/v1/recurrent-breaks/")
            )
        ):
            raise httpx.ReadTimeout("The break delete response was lost")
        if (
            lose_create_response
            and not lost_response
            and request.method == "POST"
            and request.url.path in {"/api/v1/breaks/", "/api/v1/recurrent-breaks/"}
        ):
            lost_response = True
            raise httpx.ReadTimeout("The break creation response was lost")
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


async def _open_break_list(driver: BotDriver, *, weekly: bool = False) -> None:
    await driver.text("/start")
    await driver.click("Breaks")
    driver.add_break_label = "Recurring break" if weekly else "One-time break"
    assert (
        "Manage breaks. Tap a break to remove it." in driver.session.last_message.text
    )
    assert "Showing " not in driver.session.last_message.text


async def _enter_break(
    driver: BotDriver,
    *,
    start_date: str,
    start_time: str,
    end_time: str,
    cutoff_date: str | None = None,
) -> None:
    await driver.click("Add break")
    await driver.click(driver.add_break_label)
    screen_message_id = driver.session.last_message.message_id
    assert "Timezone:" not in driver.session.last_message.text
    assert "Asia/Bangkok" not in driver.session.last_message.text
    start_message_id = driver.update_id + 1
    await driver.text(start_date)
    assert driver.deleted_message_ids()[-1] == start_message_id
    assert driver.deleted_message_ids().count(start_message_id) == 1
    assert driver.session.last_message.message_id == screen_message_id
    await driver.click("Enter time manually")
    start_time_message_id = driver.update_id + 1
    await driver.text(start_time)
    assert driver.deleted_message_ids()[-1] == start_time_message_id
    assert driver.deleted_message_ids().count(start_time_message_id) == 1
    await driver.click("Enter time manually")
    end_time_message_id = driver.update_id + 1
    await driver.text(end_time)
    assert driver.deleted_message_ids()[-1] == end_time_message_id
    assert driver.deleted_message_ids().count(end_time_message_id) == 1
    if cutoff_date is not None:
        await driver.click("Change series end")
        assert "Timezone:" not in driver.session.last_message.text
        assert "Asia/Bangkok" not in driver.session.last_message.text
        cutoff_message_id = driver.update_id + 1
        await driver.text(cutoff_date)
        assert driver.deleted_message_ids()[-1] == cutoff_message_id
        assert driver.deleted_message_ids().count(cutoff_message_id) == 1


def _event_button(driver: BotDriver) -> str:
    assert driver.session.last_message is not None
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    for row in markup.inline_keyboard:
        for button in row:
            if (
                button.text.startswith(("Weekly · ", "One-time · "))
                and button.callback_data is not None
            ):
                return button.text
    raise AssertionError(
        f"No break on the current page: {driver.session.last_message.text}"
    )


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


async def _event_exists(session: AsyncSession, event_id: UUID) -> bool:
    result = await session.execute(select(Event.id).where(Event.id == event_id))
    return result.scalar_one_or_none() is not None


async def test_teacher_creates_break_for_all_students_in_local_timezone(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session, timezone="Asia/Bangkok")
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver)
        await _enter_break(
            driver,
            start_date="04/11/2030",
            start_time="10:00",
            end_time="10:30",
        )
        assert "Timezone: Asia/Bangkok" in driver.session.last_message.text
        await driver.click("Save break")

        created = (
            await db_session.execute(
                select(Event).where(Event.event_type == EventType.BREAK.value)
            )
        ).scalar_one()
        assert created.user_id == teacher.id
        assert created.start == datetime(2030, 11, 4, 3)
        assert created.end == datetime(2030, 11, 4, 3, 30)
        assert "Break created." in driver.session.last_message.text
        assert (
            len(
                [
                    request
                    for request in requests
                    if request.method == "POST"
                    and request.url.path == "/api/v1/breaks/"
                ]
            )
            == 1
        )
        _assert_same_message(driver)
    finally:
        await _close_bot(driver, http)


async def test_weekly_break_defaults_open_ended_and_discloses_validation_bound(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    lesson = _lesson(teacher, student, datetime(2030, 11, 4, 10))
    db_session.add(lesson)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver, weekly=True)
        await _enter_break(
            driver,
            start_date="04/11/2030",
            start_time="10:00",
            end_time="10:30",
        )
        assert "No end date" in driver.session.last_message.text
        assert "Last break date" not in driver.session.last_message.text
        assert "one calendar year" not in driver.session.last_message.text

        await driver.click("Save break")
        assert "One-time lesson" in driver.session.last_message.text
        assert "one calendar year" not in driver.session.last_message.text
        await driver.click("Confirm and displace lessons")

        created = (
            await db_session.execute(
                select(RecurrentEvent).where(
                    RecurrentEvent.event_type == EventType.BREAK.value
                )
            )
        ).scalar_one()
        assert created.start == datetime(2030, 11, 4, 10)
        assert created.end == datetime(2030, 11, 4, 10, 30)
        assert created.interval == 7
        assert created.interval_end is None
        assert not await _event_exists(db_session, lesson.id)

        create_request = next(
            request
            for request in requests
            if request.method == "POST"
            and request.url.path == "/api/v1/recurrent-breaks/"
        )
        payload = json.loads(create_request.content)
        assert payload["start"] == "2030-11-04T10:00:00Z"
        assert payload["end"] == "2030-11-04T10:30:00Z"
        assert payload["interval"] == 7
        assert payload["interval_end"] is None

        await driver.click(_event_button(driver))
        assert "No end date" in driver.session.last_message.text
        assert "Last break date" not in driver.session.last_message.text
        assert "one calendar year" not in driver.session.last_message.text
        await driver.click("Edit break")
        assert "Update weekly break" in driver.session.last_message.text
        assert "No end date" in driver.session.last_message.text
        await driver.click("Save change")
        unchanged = await db_session.get(RecurrentEvent, created.id)
        assert unchanged is not None
        assert unchanged.interval_end is None
    finally:
        await _close_bot(driver, http)


async def test_break_time_input_cleanup_and_validation_failures(
    client, db_session, bot_service_key, caplog
) -> None:
    teacher, _student = await _seed_accounts(db_session, timezone="America/New_York")
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver)
        await driver.click("Add break")
        await driver.click(driver.add_break_label)
        await driver.text("10/03/2030")
        await driver.click("Enter time manually")
        initial_deletions = driver.deleted_message_ids()

        invalid_message_id = driver.update_id + 1
        screen_message_id = driver.session.last_message.message_id
        await driver.text("25:61")
        assert driver.deleted_message_ids() == initial_deletions
        assert invalid_message_id not in driver.deleted_message_ids()
        assert driver.session.last_message.message_id == screen_message_id
        assert driver.session.last_message.text.startswith("<b>❌ Error</b>")

        accepted_message_id = driver.update_id + 1
        driver.session.delete_failure = "Bad Request: message to delete not found"
        await driver.text("03:00")
        assert accepted_message_id in driver.deleted_message_ids()
        assert driver.deleted_message_ids().count(accepted_message_id) == 1
        assert "Time accepted, but Telegram could not remove your message." in (
            driver.session.last_message.text
        )
        assert driver.session.last_message.message_id == screen_message_id
        assert not any(
            request.method == "POST" and request.url.path == "/api/v1/recurrent-breaks/"
            for request in requests
        )

        await driver.click("Enter time manually")
        end_message_id = driver.update_id + 1
        await driver.text("03:30")
        assert driver.deleted_message_ids()[-1] == end_message_id
        assert driver.deleted_message_ids().count(end_message_id) == 1
        assert "Time accepted, but Telegram could not remove your message." in (
            driver.session.last_message.text
        )
        assert not any(
            request.method == "POST" and request.url.path == "/api/v1/breaks/"
            for request in requests
        )
        assert "Could not delete accepted time input" in caplog.text
    finally:
        await _close_bot(driver, http)


async def test_backend_rejected_break_times_are_not_deleted_on_create_or_edit(
    client, db_session, bot_service_key
) -> None:
    teacher, _student = await _seed_accounts(db_session)
    existing = _break(teacher, datetime(2030, 11, 4, 10), datetime(2030, 11, 4, 10, 30))
    assert isinstance(existing, Event)
    db_session.add(existing)
    await db_session.commit()
    driver, _api, http, _requests = await _make_bot(
        client,
        bot_service_key,
        _telegram_id(teacher),
        resolution_failure_status=422,
    )
    try:
        await _open_break_list(driver)
        await driver.click("Add break")
        await driver.click(driver.add_break_label)
        await driver.text("04/11/2030")
        await driver.click("Enter time manually")
        initial_deletions = driver.deleted_message_ids()
        create_message_id = driver.update_id + 1
        screen_message_id = driver.session.last_message.message_id
        await driver.text("11:00")
        assert driver.deleted_message_ids() == initial_deletions
        assert create_message_id not in driver.deleted_message_ids()
        assert driver.session.last_message.message_id == screen_message_id
        assert "Check the date, time" in driver.session.last_message.text

        await driver.click("Cancel")
        await driver.click(_event_button(driver))
        await driver.click("Edit break")
        await driver.click("Change start")
        await driver.click("Enter time manually")
        edit_message_id = driver.update_id + 1
        screen_message_id = driver.session.last_message.message_id
        await driver.text("11:00")
        assert driver.deleted_message_ids() == initial_deletions
        assert edit_message_id not in driver.deleted_message_ids()
        assert driver.session.last_message.message_id == screen_message_id
        assert "Check the date, time" in driver.session.last_message.text
        unchanged = await db_session.get(Event, existing.id)
        assert unchanged is not None
        assert unchanged.start == datetime(2030, 11, 4, 10)
        assert unchanged.end == datetime(2030, 11, 4, 10, 30)
    finally:
        await _close_bot(driver, http)


async def test_student_does_not_see_breaks_or_break_details(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    db_session.add(
        _break(teacher, datetime(2030, 11, 4, 10), datetime(2030, 11, 4, 11))
    )
    await db_session.commit()
    driver, api, http, _ = await _make_bot(
        client, bot_service_key, _telegram_id(student)
    )
    try:
        await driver.text("/start")
        assert "Breaks" not in driver.session.last_message.text
        assert driver.session.last_message.reply_markup is not None
        assert all(
            button.text != "Breaks"
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        )

        token = api.tokens[_telegram_id(student)]
        response = await client.get(
            "/api/v1/schedule/week",
            params={"date": "2030-11-04"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        assert [item for day in response.json()["days"] for item in day["items"]] == []

        try:
            managed_breaks = await api.read_management_page(
                _telegram_id(student), _telegram_id(student), "breaks", limit=20
            )
        except BackendError as error:
            assert error.status == 403
        else:
            assert managed_breaks.items == []

        with pytest.raises(BackendError) as conflict:
            await api.write_management_event(
                "POST",
                "lessons",
                _telegram_id(student),
                _telegram_id(student),
                EventCreatePayload(
                    start=datetime(2030, 11, 4, 10, tzinfo=UTC),
                    end=datetime(2030, 11, 4, 11, tzinfo=UTC),
                    user_id=student.id,
                    event_type="LESSON",
                ),
                EventWriteResult,
            )
        assert conflict.value.status in {409, 422}
        assert "break" not in str(conflict.value).casefold()

        with pytest.raises(BackendError) as error:
            await api.write_management_event(
                "POST",
                "breaks",
                _telegram_id(student),
                _telegram_id(student),
                EventCreatePayload(
                    start=datetime(2030, 11, 5, 10, tzinfo=UTC),
                    end=datetime(2030, 11, 5, 10, 30, tzinfo=UTC),
                    user_id=student.id,
                    event_type="BREAK",
                ),
                EventWriteResult,
            )
        assert error.value.status == 403
        count_breaks = (
            (
                await db_session.execute(
                    select(Event).where(Event.event_type == EventType.BREAK.value)
                )
            )
            .scalars()
            .all()
        )
        assert len(count_breaks) == 1
    finally:
        await _close_bot(driver, http)


async def test_displacement_preview_cancel_performs_no_write(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    lesson = _lesson(teacher, student, datetime(2030, 11, 4, 10))
    db_session.add(lesson)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver)
        await _enter_break(
            driver,
            start_date="04/11/2030",
            start_time="10:00",
            end_time="10:30",
        )
        await driver.click("Save break")
        assert "One-time lesson" in driver.session.last_message.text
        assert "permanently deletes" in driver.session.last_message.text
        assert await db_session.get(Event, lesson.id) is not None
        assert (
            await db_session.execute(
                select(Event).where(Event.event_type == EventType.BREAK.value)
            )
        ).scalar_one_or_none() is None
        post_count = sum(
            request.method == "POST" and request.url.path == "/api/v1/breaks/"
            for request in requests
        )
        assert post_count == 1

        await driver.click("Cancel; make no changes")
        assert "No break changes were made." in driver.session.last_message.text
        assert await db_session.get(Event, lesson.id) is not None
        assert (
            await db_session.execute(
                select(Event).where(Event.event_type == EventType.BREAK.value)
            )
        ).scalar_one_or_none() is None
        assert (
            sum(
                request.method == "POST" and request.url.path == "/api/v1/breaks/"
                for request in requests
            )
            == post_count
        )
        _assert_same_message(driver)
    finally:
        await _close_bot(driver, http)


async def test_confirmed_weekly_break_deletes_exact_lessons_and_cancels_occurrences(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    one_time = _lesson(teacher, student, datetime(2030, 11, 5, 10))
    one_time.post_break_minutes = 5
    series = _weekly_lesson(
        teacher,
        student,
        datetime(2030, 11, 5, 10),
        interval_end=datetime(2030, 11, 26, 10),
    )
    db_session.add_all([one_time, series])
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver, weekly=True)
        await _enter_break(
            driver,
            start_date="05/11/2030",
            start_time="10:00",
            end_time="10:30",
            cutoff_date="05/11/2030",
        )
        await driver.click("Save break")
        preview_text = driver.session.last_message.text
        assert "One-time lesson" in preview_text
        assert "Auto break: 5 minutes" in preview_text
        assert "Automatic break reservation" not in preview_text
        assert "Affected lessons" not in preview_text
        assert "Nov 5" in preview_text
        assert "November" not in preview_text
        assert "\n\nConfirming permanently deletes" in preview_text
        assert "(UTC" not in preview_text
        assert "Weekly lesson" in preview_text
        assert await db_session.get(Event, one_time.id) is not None
        assert (
            not (
                await db_session.execute(
                    select(RecurrentCancels).where(
                        RecurrentCancels.recurrent_event_id == series.id
                    )
                )
            )
            .scalars()
            .all()
        )

        await driver.click("Confirm and displace lessons")
        assert "Break created." in driver.session.last_message.text
        assert not await _event_exists(db_session, one_time.id)
        cancellations = (
            (
                await db_session.execute(
                    select(RecurrentCancels).where(
                        RecurrentCancels.recurrent_event_id == series.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [(item.start, item.end) for item in cancellations] == [
            (datetime(2030, 11, 5, 10), datetime(2030, 11, 5, 11))
        ]
        created_break = (
            await db_session.execute(
                select(RecurrentEvent).where(
                    RecurrentEvent.event_type == EventType.BREAK.value
                )
            )
        ).scalar_one()
        assert created_break.user_id == teacher.id
        assert created_break.interval == 7
        assert created_break.interval_end == datetime(2030, 11, 6)

        writes = [
            request
            for request in requests
            if request.method == "POST"
            and request.url.path == "/api/v1/recurrent-breaks/"
        ]
        assert len(writes) == 2
        assert writes[0].url.params.get("confirm_displacement") is None
        assert writes[1].url.params.get("confirm_displacement") == "true"
        assert writes[1].headers.get("x-displacement-token")
        assert len(writes[1].headers["x-displacement-token"]) == 64
        assert writes[0].content == writes[1].content
    finally:
        await _close_bot(driver, http)


async def test_displacement_preview_is_paged_and_revalidated_before_second_confirm(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    first_day = datetime(2030, 11, 5, 10)
    lessons = [
        _lesson(teacher, student, first_day + timedelta(days=7 * index))
        for index in range(11)
    ]
    db_session.add_all(lessons)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver, weekly=True)
        await _enter_break(
            driver,
            start_date="05/11/2030",
            start_time="10:00",
            end_time="10:30",
            cutoff_date="14/01/2031",
        )
        markup = driver.session.last_message.reply_markup
        assert markup is not None
        change_rows = [
            [button.text for button in row]
            for row in markup.inline_keyboard
            if any(button.text.startswith("Change") for button in row)
        ]
        assert change_rows == [
            ["Change date", "Change start"],
            ["Change end", "Change series end"],
        ]
        await driver.click("Save break")
        assert "Affected lessons" not in driver.session.last_message.text
        assert "Showing" not in driver.session.last_message.text
        await driver.click("Next ▶")
        assert "Affected lessons" not in driver.session.last_message.text
        assert driver.session.last_message.text.count("One-time lesson") == 1
        await driver.click("Confirm and displace lessons")
        assert "Break created." in driver.session.last_message.text
        remaining = (
            (
                await db_session.execute(
                    select(Event).where(Event.event_type == EventType.LESSON.value)
                )
            )
            .scalars()
            .all()
        )
        assert not remaining
        assert (
            sum(
                request.method == "POST"
                and request.url.path == "/api/v1/recurrent-breaks/"
                for request in requests
            )
            == 2
        )
    finally:
        await _close_bot(driver, http)


async def test_changed_displacement_preview_requires_a_new_confirmation(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    first = _lesson(teacher, student, datetime(2030, 11, 4, 10))
    db_session.add(first)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver)
        await _enter_break(
            driver,
            start_date="04/11/2030",
            start_time="10:00",
            end_time="10:30",
        )
        await driver.click("Save break")
        second = _lesson(teacher, student, datetime(2030, 11, 4, 10, 5))
        db_session.add(second)
        await db_session.commit()

        await driver.click("Confirm and displace lessons")
        assert "One-time lesson" in driver.session.last_message.text
        assert driver.session.last_message.text.count("One-time lesson") == 2
        assert await db_session.get(Event, first.id) is not None
        assert await db_session.get(Event, second.id) is not None
        assert (
            await db_session.execute(
                select(Event).where(Event.event_type == EventType.BREAK.value)
            )
        ).scalar_one_or_none() is None
        assert (
            sum(
                request.method == "POST" and request.url.path == "/api/v1/breaks/"
                for request in requests
            )
            == 2
        )

        await driver.click("Confirm and displace lessons")
        assert "Break created." in driver.session.last_message.text
        assert not await _event_exists(db_session, first.id)
        assert not await _event_exists(db_session, second.id)
        assert (
            sum(
                request.method == "POST" and request.url.path == "/api/v1/breaks/"
                for request in requests
            )
            == 3
        )
    finally:
        await _close_bot(driver, http)


async def test_editing_break_confirms_displacement_and_sends_revision(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    existing = _break(teacher, datetime(2030, 11, 4, 12), datetime(2030, 11, 4, 12, 30))
    lesson = _lesson(teacher, student, datetime(2030, 11, 4, 10))
    db_session.add_all([existing, lesson])
    await db_session.commit()
    assert isinstance(existing, Event)
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver)
        await driver.click(_event_button(driver))
        await driver.click("Edit break")
        await driver.click("Change start")
        await driver.click("Enter time manually")
        start_message_id = driver.update_id + 1
        await driver.text("10:00")
        assert driver.deleted_message_ids()[-1] == start_message_id
        assert driver.deleted_message_ids().count(start_message_id) == 1
        await driver.click("Change end")
        await driver.click("Enter time manually")
        end_message_id = driver.update_id + 1
        await driver.text("10:30")
        assert driver.deleted_message_ids()[-1] == end_message_id
        assert driver.deleted_message_ids().count(end_message_id) == 1
        await driver.click("Save change")
        assert "One-time lesson" in driver.session.last_message.text
        assert await db_session.get(Event, lesson.id) is not None

        await driver.click("Confirm and displace lessons")
        updated = (
            await db_session.execute(
                select(Event.start, Event.end).where(Event.id == existing.id)
            )
        ).one_or_none()
        assert updated == (
            datetime(2030, 11, 4, 10),
            datetime(2030, 11, 4, 10, 30),
        )
        assert not await _event_exists(db_session, lesson.id)
        update_requests = [
            request
            for request in requests
            if request.method == "PUT"
            and request.url.path == f"/api/v1/breaks/{existing.id}"
        ]
        assert len(update_requests) == 2
        assert update_requests[0].headers.get("if-match")
        assert update_requests[1].headers.get("if-match") == update_requests[
            0
        ].headers.get("if-match")
        assert update_requests[1].url.params.get("confirm_displacement") == "true"
        assert update_requests[1].headers.get("x-displacement-token")
    finally:
        await _close_bot(driver, http)


async def test_weekly_break_edit_preserves_interval_and_deletes_in_one_tap(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    existing = _break(
        teacher,
        datetime(2030, 11, 5, 10),
        datetime(2030, 11, 5, 10, 30),
        interval=7,
        interval_end=datetime(2030, 12, 1),
    )
    assert isinstance(existing, RecurrentEvent)
    db_session.add(existing)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        await driver.click("Edit break")
        await driver.click("Change start")
        await driver.click("Enter time manually")
        start_message_id = driver.update_id + 1
        await driver.text("11:00")
        assert driver.deleted_message_ids()[-1] == start_message_id
        assert driver.deleted_message_ids().count(start_message_id) == 1
        await driver.click("Enter time manually")
        end_message_id = driver.update_id + 1
        await driver.text("11:30")
        assert driver.deleted_message_ids()[-1] == end_message_id
        assert driver.deleted_message_ids().count(end_message_id) == 1
        await driver.click("Change series end")
        await driver.text("26/11/2030")
        assert "does not restore lessons previously displaced" in (
            driver.session.last_message.text
        )
        await driver.click("Save change")
        updated = (
            await db_session.execute(
                select(
                    RecurrentEvent.start,
                    RecurrentEvent.end,
                    RecurrentEvent.interval,
                    RecurrentEvent.interval_end,
                ).where(RecurrentEvent.id == existing.id)
            )
        ).one()
        assert updated == (
            datetime(2030, 11, 5, 11),
            datetime(2030, 11, 5, 11, 30),
            7,
            datetime(2030, 11, 27),
        )
        update_request = next(
            request
            for request in requests
            if request.method == "PUT"
            and request.url.path == f"/api/v1/recurrent-breaks/{existing.id}"
        )
        assert update_request.headers.get("if-match")
        assert "Break updated." in driver.session.last_message.text

        await driver.click("Delete break")
        exists = await db_session.execute(
            select(RecurrentEvent.id).where(RecurrentEvent.id == existing.id)
        )
        assert exists.scalar_one_or_none() is None
        assert "Break deleted." in driver.session.last_message.text
    finally:
        await _close_bot(driver, http)


async def test_weekly_break_can_clear_a_finite_cutoff(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    existing = _break(
        teacher,
        datetime(2030, 11, 5, 10),
        datetime(2030, 11, 5, 10, 30),
        interval=7,
        interval_end=datetime(2030, 12, 1),
    )
    assert isinstance(existing, RecurrentEvent)
    db_session.add(existing)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        await driver.click("Edit break")
        await driver.click("Change series end")
        await driver.click("No end")
        assert "No end date" in driver.session.last_message.text
        assert "Last break date" not in driver.session.last_message.text
        assert "one calendar year" not in driver.session.last_message.text
        await driver.click("Save change")

        updated = await db_session.execute(
            select(
                RecurrentEvent.start,
                RecurrentEvent.end,
                RecurrentEvent.interval,
                RecurrentEvent.interval_end,
            ).where(RecurrentEvent.id == existing.id)
        )
        assert updated.one() == (
            datetime(2030, 11, 5, 10),
            datetime(2030, 11, 5, 10, 30),
            7,
            None,
        )
        update_request = next(
            request
            for request in requests
            if request.method == "PUT"
            and request.url.path == f"/api/v1/recurrent-breaks/{existing.id}"
        )
        assert json.loads(update_request.content)["interval_end"] is None
    finally:
        await _close_bot(driver, http)


async def test_cutoff_only_weekly_edit_preserves_interval_endpoints(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    existing = _break(
        teacher,
        datetime(2030, 11, 5, 10),
        datetime(2030, 11, 5, 10, 30),
        interval=7,
        interval_end=datetime(2030, 12, 1),
    )
    assert isinstance(existing, RecurrentEvent)
    db_session.add(existing)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        await driver.click("Edit break")
        await driver.click("Change series end")
        cutoff_message_id = driver.update_id + 1
        await driver.text("15/12/2030")
        assert driver.deleted_message_ids()[-1] == cutoff_message_id
        assert driver.deleted_message_ids().count(cutoff_message_id) == 1
        assert "10:00 - 10:30" in driver.session.last_message.text
        await driver.click("Save change")

        updated = await db_session.execute(
            select(
                RecurrentEvent.start,
                RecurrentEvent.end,
                RecurrentEvent.interval,
                RecurrentEvent.interval_end,
            ).where(RecurrentEvent.id == existing.id)
        )
        assert updated.one() == (
            datetime(2030, 11, 5, 10),
            datetime(2030, 11, 5, 10, 30),
            7,
            datetime(2030, 12, 16),
        )
        update_request = next(
            request
            for request in requests
            if request.method == "PUT"
            and request.url.path == f"/api/v1/recurrent-breaks/{existing.id}"
        )
        payload = json.loads(update_request.content)
        assert payload["start"] == "2030-11-05T10:00:00Z"
        assert payload["end"] == "2030-11-05T10:30:00Z"
        assert payload["interval"] == 7
        assert payload["interval_end"] == "2030-12-16T00:00:00Z"
    finally:
        await _close_bot(driver, http)


async def test_new_start_after_finite_cutoff_blocks_save_until_cutoff_is_fixed(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    existing = _break(
        teacher,
        datetime(2030, 11, 5, 10),
        datetime(2030, 11, 5, 10, 30),
        interval=7,
        interval_end=datetime(2030, 12, 1),
    )
    assert isinstance(existing, RecurrentEvent)
    db_session.add(existing)
    await db_session.commit()
    driver, _api, http, _requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        await driver.click("Edit break")
        await driver.click("Change date")
        await driver.text("02/12/2030")
        assert "no longer includes the first break" in driver.session.last_message.text
        markup = driver.session.last_message.reply_markup
        assert markup is not None
        assert all(
            button.text != "Save change"
            for row in markup.inline_keyboard
            for button in row
        )

        await driver.click("Change series end")
        await driver.text("20/12/2030")
        assert driver.callback_data("Save change")
        await driver.click("Save change")

        updated = await db_session.execute(
            select(
                RecurrentEvent.start,
                RecurrentEvent.end,
                RecurrentEvent.interval,
                RecurrentEvent.interval_end,
            ).where(RecurrentEvent.id == existing.id)
        )
        assert updated.one() == (
            datetime(2030, 12, 2, 10),
            datetime(2030, 12, 2, 10, 30),
            7,
            datetime(2030, 12, 21),
        )
    finally:
        await _close_bot(driver, http)


async def test_canceling_a_changed_break_edit_does_not_write(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    existing = _break(teacher, datetime(2030, 11, 5, 12), datetime(2030, 11, 5, 12, 30))
    assert isinstance(existing, Event)
    db_session.add(existing)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver)
        await driver.click(_event_button(driver))
        await driver.click("Edit break")
        await driver.click("Change start")
        await driver.click("Enter time manually")
        await driver.text("10:00")
        assert "10:00 - 12:30" in driver.session.last_message.text
        await driver.click("Cancel")

        unchanged = await db_session.execute(
            select(Event.start, Event.end).where(Event.id == existing.id)
        )
        assert unchanged.one() == (
            datetime(2030, 11, 5, 12),
            datetime(2030, 11, 5, 12, 30),
        )
        assert not any(
            request.method == "PUT"
            and request.url.path == f"/api/v1/breaks/{existing.id}"
            for request in requests
        )
    finally:
        await _close_bot(driver, http)


async def test_stale_break_edit_requires_reload_without_replaying_write(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    existing = _break(teacher, datetime(2030, 11, 5, 12), datetime(2030, 11, 5, 12, 30))
    assert isinstance(existing, Event)
    db_session.add(existing)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client,
        bot_service_key,
        _telegram_id(teacher),
        stale_update_once=True,
    )
    try:
        await _open_break_list(driver)
        await driver.click(_event_button(driver))
        await driver.click("Edit break")
        await driver.click("Change start")
        await driver.click("Enter time manually")
        await driver.text("10:00")
        await driver.click("Save change")
        assert "Reload" in [
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        ]
        assert "This event changed. Reload it before editing." in (
            driver.session.last_message.text
        )
        await driver.click("Reload")
        assert "Current break details were reloaded." in (
            driver.session.last_message.text
        )
        assert "No write was repeated." in driver.session.last_message.text
        assert (
            sum(
                request.method == "PUT"
                and request.url.path == f"/api/v1/breaks/{existing.id}"
                for request in requests
            )
            == 1
        )
        unchanged = await db_session.execute(
            select(Event.start, Event.end).where(Event.id == existing.id)
        )
        assert unchanged.one() == (
            datetime(2030, 11, 5, 12),
            datetime(2030, 11, 5, 12, 30),
        )
    finally:
        await _close_bot(driver, http)


async def test_legacy_recurrent_break_edit_preserves_interval_timezone_and_cutoff(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session, timezone="America/Chicago")
    original_day = date.today() + timedelta(days=1)
    original_start = datetime.combine(original_day, time(16))
    interval_end = datetime.combine(original_day + timedelta(days=80), time(6))
    existing = _break(
        teacher,
        original_start,
        original_start + timedelta(minutes=30),
        interval=14,
        interval_end=interval_end,
    )
    assert isinstance(existing, RecurrentEvent)
    existing.recurrence_mode = "TEACHER_LOCAL"
    existing.recurrence_timezone = "America/Chicago"
    db_session.add(existing)
    await db_session.commit()
    teacher.timezone = "UTC"
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        await driver.click("Edit break")
        await driver.click("Change date")
        start_message_id = driver.update_id + 1
        new_start_day = original_day + timedelta(days=1)
        await driver.text(
            f"{new_start_day.day:02}/{new_start_day.month:02}/{new_start_day.year}"
        )
        assert driver.deleted_message_ids()[-1] == start_message_id
        assert driver.deleted_message_ids().count(start_message_id) == 1
        assert "16:00 - 16:30" in driver.session.last_message.text

        await driver.click("Change series end")
        markup = driver.session.last_message.reply_markup
        assert markup is not None
        assert all(
            button.text != "No end" for row in markup.inline_keyboard for button in row
        )
        inferred_cutoff = date.today() - timedelta(days=60)
        await driver.text(f"{inferred_cutoff.day:02}/{inferred_cutoff.month:02}")
        assert "is in the next year" in driver.session.last_message.text
        await driver.click("Back")
        assert "16:00 - 16:30" in driver.session.last_message.text
        assert "Last break date (inclusive):" in driver.session.last_message.text

        await driver.click("Save change")
        updated = (
            await db_session.execute(
                select(
                    RecurrentEvent.start,
                    RecurrentEvent.end,
                    RecurrentEvent.interval,
                    RecurrentEvent.interval_end,
                    RecurrentEvent.recurrence_mode,
                    RecurrentEvent.recurrence_timezone,
                ).where(RecurrentEvent.id == existing.id)
            )
        ).one()
        assert updated == (
            datetime.combine(new_start_day, time(16)),
            datetime.combine(new_start_day, time(16, 30)),
            14,
            interval_end,
            "TEACHER_LOCAL",
            "America/Chicago",
        )
        update_request = next(
            request
            for request in requests
            if request.method == "PUT"
            and request.url.path == f"/api/v1/recurrent-breaks/{existing.id}"
        )
        payload = json.loads(update_request.content)
        assert payload["interval"] == 14
        assert datetime.fromisoformat(payload["interval_end"]) == interval_end.replace(
            tzinfo=UTC
        )
        assert datetime.fromisoformat(payload["start"]) == datetime.combine(
            new_start_day, time(16), tzinfo=UTC
        )
        assert datetime.fromisoformat(payload["end"]) == datetime.combine(
            new_start_day, time(16, 30), tzinfo=UTC
        )
    finally:
        await _close_bot(driver, http)


async def test_generic_schedule_conflict_does_not_show_displacement_confirmation(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    db_session.add(
        _break(teacher, datetime(2030, 11, 4, 10), datetime(2030, 11, 4, 10, 30))
    )
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver)
        await _enter_break(
            driver,
            start_date="04/11/2030",
            start_time="10:15",
            end_time="10:45",
        )
        await driver.click("Save break")
        assert (
            "That time overlaps another scheduled event."
            in driver.session.last_message.text
        )
        assert all(
            button.text != "Confirm and displace lessons"
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        )
        assert (
            sum(
                request.method == "POST" and request.url.path == "/api/v1/breaks/"
                for request in requests
            )
            == 1
        )
        assert (
            len(
                (
                    await db_session.execute(
                        select(Event).where(Event.event_type == EventType.BREAK.value)
                    )
                )
                .scalars()
                .all()
            )
            == 1
        )
    finally:
        await _close_bot(driver, http)


async def test_deleting_break_is_immediate_and_does_not_restore_lessons(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    series = _weekly_lesson(
        teacher,
        student,
        datetime(2030, 11, 4, 10),
        interval_end=datetime(2030, 11, 25, 10),
    )
    break_event = _break(
        teacher,
        datetime(2030, 11, 4, 10),
        datetime(2030, 11, 4, 10, 30),
    )
    assert isinstance(break_event, Event)
    db_session.add_all(
        [
            series,
            break_event,
        ]
    )
    await db_session.flush()
    db_session.add(
        RecurrentCancels(
            recurrent_event_id=series.id,
            start=datetime(2030, 11, 4, 10),
            end=datetime(2030, 11, 4, 11),
        )
    )
    await db_session.commit()
    driver, _api, http, _requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver)
        await driver.click(_event_button(driver))
        await driver.click("Delete break")
        assert not await _event_exists(db_session, break_event.id)
        assert (
            await db_session.execute(
                select(RecurrentCancels).where(
                    RecurrentCancels.recurrent_event_id == series.id,
                    RecurrentCancels.start == datetime(2030, 11, 4, 10),
                )
            )
        ).scalar_one_or_none() is not None
        assert (
            "Displaced lessons were not restored." in driver.session.last_message.text
        )
    finally:
        await _close_bot(driver, http)


async def test_lost_break_create_response_is_not_replayed(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    driver, _api, http, requests = await _make_bot(
        client,
        bot_service_key,
        _telegram_id(teacher),
        lose_create_response=True,
    )
    try:
        await _open_break_list(driver)
        await _enter_break(
            driver,
            start_date="04/11/2030",
            start_time="10:00",
            end_time="10:30",
        )
        await driver.click("Save break")
        unknown = (
            "The save outcome is unknown. Reload the latest data before trying "
            "again; do not repeat the action."
        )
        assert driver.session.last_message.text.startswith(
            f"<b>⚠️ Attention</b>\n{unknown}\n\n"
        )
        assert driver.session.last_message.text.count(unknown) == 1
        assert (
            len(
                (
                    await db_session.execute(
                        select(Event.id).where(
                            Event.event_type == EventType.BREAK.value
                        )
                    )
                )
                .scalars()
                .all()
            )
            == 1
        )
        await driver.click("Reload")
        assert driver.session.last_message.text.startswith(
            "<b>⚠️ Attention</b>\nThe break list was reloaded."
        )
        assert "check it before retrying" in driver.session.last_message.text.casefold()
        assert (
            sum(
                request.method == "POST" and request.url.path == "/api/v1/breaks/"
                for request in requests
            )
            == 1
        )
        assert (
            len(
                (
                    await db_session.execute(
                        select(Event).where(Event.event_type == EventType.BREAK.value)
                    )
                )
                .scalars()
                .all()
            )
            == 1
        )
    finally:
        await _close_bot(driver, http)


async def test_break_list_is_one_column_sorted_with_short_labels_and_add_choice(
    client, db_session, bot_service_key
) -> None:
    teacher, _student = await _seed_accounts(db_session)
    db_session.add_all(
        [
            _break(teacher, datetime(2030, 10, 20, 9), datetime(2030, 10, 20, 9, 30)),
            _break(
                teacher,
                datetime(2030, 10, 14, 9),
                datetime(2030, 10, 14, 9, 15),
                interval=7,
                interval_end=datetime(2030, 11, 30, 9),
            ),
            _break(teacher, datetime(2030, 10, 26, 9), datetime(2030, 10, 26, 9, 30)),
        ]
    )
    await db_session.commit()
    driver, _api, http, _requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver)
        text = driver.session.last_message.text
        assert "Manage breaks. Tap a break to remove it." in text
        assert "October" not in text
        markup = driver.session.last_message.reply_markup
        assert markup is not None
        rows = [[button.text for button in row] for row in markup.inline_keyboard]
        assert rows == [
            ["Add break"],
            ["Weekly · Oct 14 (2030) 9:00 - 9:15"],
            ["One-time · Oct 20 (2030) 9:00 - 9:30"],
            ["One-time · Oct 26 (2030) 9:00 - 9:30"],
            ["Back to home"],
        ]

        await driver.click("Add break")
        assert _button_labels(driver) == ["One-time break", "Recurring break", "Back"]
        await driver.click("Back")
        await driver.click("Weekly · Oct 14 (2030) 9:00 - 9:15")
        assert "Edit break" in _button_labels(driver)
        assert "Delete break" in _button_labels(driver)
    finally:
        await _close_bot(driver, http)


def _button_labels(driver: BotDriver) -> list[str]:
    assert driver.session.last_message is not None
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    return [button.text for row in markup.inline_keyboard for button in row]


async def test_break_delete_unknown_outcome_stays_on_detail_without_replay(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    break_event = _break(
        teacher, datetime(2030, 11, 4, 10), datetime(2030, 11, 4, 10, 30)
    )
    db_session.add(break_event)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher), delete_failure="lost"
    )
    try:
        await _open_break_list(driver)
        await driver.click(_event_button(driver))
        await driver.click("Delete break")
        text = driver.session.last_message.text
        assert "Attention" in text
        assert "outcome is unknown" in text
        labels = _button_labels(driver)
        assert "Reload break" in labels
        assert "Delete break" not in labels
        assert "Edit break" not in labels
        assert sum(request.method == "DELETE" for request in requests) == 1

        await driver.click("Reload break")
        assert "no longer available" in driver.session.last_message.text
        assert sum(request.method == "DELETE" for request in requests) == 1
    finally:
        await _close_bot(driver, http)


async def test_break_delete_stale_revision_requires_reload(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    break_event = _break(
        teacher, datetime(2030, 11, 4, 10), datetime(2030, 11, 4, 10, 30)
    )
    db_session.add(break_event)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher), delete_failure="stale"
    )
    try:
        await _open_break_list(driver)
        await driver.click(_event_button(driver))
        await driver.click("Delete break")
        assert "This event changed. Reload it" in driver.session.last_message.text
        labels = _button_labels(driver)
        assert "Reload break" in labels
        assert "Delete break" not in labels
        assert await db_session.get(Event, break_event.id) is not None
        await driver.click("Reload break")
        assert "Current break details were reloaded" in (
            driver.session.last_message.text
        )
        assert "Delete break" in _button_labels(driver)
        assert sum(request.method == "DELETE" for request in requests) == 1
    finally:
        await _close_bot(driver, http)


async def test_break_delete_double_tap_sends_one_delete(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    break_event = _break(
        teacher, datetime(2030, 11, 4, 10), datetime(2030, 11, 4, 10, 30)
    )
    db_session.add(break_event)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver)
        await driver.click(_event_button(driver))
        callback = driver.callback_data("Delete break")
        await driver.click("Delete break")
        await driver.click(data=callback)
        assert sum(request.method == "DELETE" for request in requests) == 1
        assert not await _event_exists(db_session, break_event.id)
    finally:
        await _close_bot(driver, http)


def _times(driver: BotDriver) -> list[str]:
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    return [
        button.text
        for row in markup.inline_keyboard
        for button in row
        if button.text[:1].isdigit()
    ]


def _labels(driver: BotDriver) -> list[str]:
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    return [button.text for row in markup.inline_keyboard for button in row]


async def _start_picker(driver: BotDriver, date_text: str = "04/11/2030") -> None:
    await _open_break_list(driver)
    await driver.click("Add break")
    await driver.click(driver.add_break_label)
    await driver.text(date_text)


async def test_break_pickers_offer_only_free_start_and_end_times(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    lesson = _lesson(teacher, student, datetime(2030, 11, 4, 11))
    db_session.add(lesson)
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _start_picker(driver)
        assert "Choose the break start time." in driver.session.last_message.text
        starts = _times(driver)
        assert starts[0] == "09:00" and "Next ▶" in _labels(driver)
        assert "10:45" in starts and "12:00" in starts
        assert not {"11:00", "11:15", "11:30", "11:45"} & set(starts)
        await driver.click("Next ▶")
        assert _times(driver)[-1] == "17:45"
        await driver.click("◀ Previous")
        query = next(
            request.url.params
            for request in requests
            if request.url.path == "/api/v1/events/available-start-times"
        )
        assert query["event_type"] == "BREAK" and "duration_minutes" not in query

        await driver.click("10:00")
        assert "Choose the break end time." in driver.session.last_message.text
        assert _times(driver) == ["10:15", "10:30", "10:45", "11:00"]
        assert {"Enter time manually", "Change start time"} <= set(_labels(driver))
        await driver.click("11:00")
        assert "10:00 - 11:00" in driver.session.last_message.text
        await driver.click("Save break")
        assert "Break created." in driver.session.last_message.text
        assert "Confirm and displace" not in driver.session.last_message.text
        created = (
            await db_session.execute(
                select(Event).where(Event.event_type == EventType.BREAK.value)
            )
        ).scalar_one()
        assert created.start == datetime(2030, 11, 4, 10)
        assert created.end == datetime(2030, 11, 4, 11)
        assert await _event_exists(db_session, lesson.id)
    finally:
        await _close_bot(driver, http)


async def test_break_picker_five_minute_toggle_persists_and_resets(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    driver, _api, http, _requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _start_picker(driver)
        assert "09:05" not in _times(driver)
        await driver.click("Show 5-minute times")
        assert _times(driver)[:3] == ["09:00", "09:05", "09:10"]
        await driver.click("Change date")
        await driver.text("05/11/2030")
        assert "09:05" in _times(driver)
        await driver.click("09:05")
        assert _times(driver)[0] == "09:10"
        assert "Show 15-minute times" in _labels(driver)
        await driver.click("Show 15-minute times")
        assert _times(driver)[0] == "09:15"
        await driver.click("Cancel")

        await driver.click("Add break")
        await driver.click(driver.add_break_label)
        await driver.text("05/11/2030")
        assert "09:05" not in _times(driver)
        assert "Show 5-minute times" in _labels(driver)
    finally:
        await _close_bot(driver, http)


async def test_break_manual_entry_keeps_displacement_reachable(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    lesson = _lesson(teacher, student, datetime(2030, 11, 4, 11))
    db_session.add(lesson)
    await db_session.commit()
    driver, _api, http, _requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _start_picker(driver)
        await driver.click("Enter time manually")
        assert "Enter the break start time" in driver.session.last_message.text
        await driver.click("Back to times")
        assert "09:00" in _times(driver)
        await driver.click("Enter time manually")
        await driver.text("11:15")
        assert "No end time fits after this start" in driver.session.last_message.text
        await driver.click("Enter time manually")
        assert "Enter the break end time" in driver.session.last_message.text
        await driver.click("Back to times")
        await driver.click("Enter time manually")
        await driver.text("11:45")
        await driver.click("Save break")
        assert "One-time lesson" in driver.session.last_message.text
        assert "Confirm and displace lessons" in _labels(driver)
        assert await _event_exists(db_session, lesson.id)
    finally:
        await _close_bot(driver, http)


async def test_break_picker_empty_day_and_outages_offer_retry(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    driver, api, http, _requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    start_original = api.read_available_start_times
    end_original = api.read_available_break_end_times
    failures = {"start": 1, "end": 1}

    async def flaky_start(*args, **kwargs):
        if failures["start"]:
            failures["start"] -= 1
            raise BackendUnavailable()
        return await start_original(*args, **kwargs)

    async def flaky_end(*args, **kwargs):
        if failures["end"]:
            failures["end"] -= 1
            raise BackendUnavailable()
        return await end_original(*args, **kwargs)

    monkeypatch.setattr(api, "read_available_start_times", flaky_start)
    monkeypatch.setattr(api, "read_available_break_end_times", flaky_end)
    try:
        await _start_picker(driver, "03/11/2030")
        assert "Backend unavailable." in driver.session.last_message.text
        assert {"Retry", "Enter time manually"} <= set(_labels(driver))
        await driver.click("Retry")
        assert "No free break times on" in driver.session.last_message.text
        assert "Choose another date." in driver.session.last_message.text
        assert not _times(driver)

        await driver.click("Change date")
        await driver.text("04/11/2030")
        assert "09:00" in _times(driver)
        await driver.click("09:00")
        assert "Backend unavailable." in driver.session.last_message.text
        assert {"Retry", "Enter time manually", "Change start time"} <= set(
            _labels(driver)
        )
        await driver.click("Retry")
        assert _times(driver)[0] == "09:15"
    finally:
        await _close_bot(driver, http)


async def test_break_edit_offers_its_own_time_and_legacy_seconds_stay_typed(
    client, db_session, bot_service_key
) -> None:
    teacher, _ = await _seed_accounts(db_session)
    existing = _break(teacher, datetime(2030, 11, 4, 10), datetime(2030, 11, 4, 10, 30))
    legacy = _break(
        teacher,
        datetime(2030, 11, 5, 10, 0, 30),
        datetime(2030, 11, 5, 10, 30, 30),
    )
    db_session.add_all([existing, legacy])
    await db_session.commit()
    driver, _api, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_break_list(driver)
        await driver.click("One-time · Nov 4 (2030) 10:00 - 10:30")
        await driver.click("Edit break")
        await driver.click("Change start")
        assert "10:00" in _times(driver)
        query = [
            request.url.params
            for request in requests
            if request.url.path == "/api/v1/events/available-start-times"
        ][-1]
        assert query["exclude_event_id"] == str(existing.id)
        await driver.click("10:15")
        assert "10:15 - 10:30" in driver.session.last_message.text
        await driver.click("Change end")
        assert "10:30" in _times(driver)
        await driver.click("11:00")
        await driver.click("Save change")
        await db_session.refresh(existing)
        assert (existing.start, existing.end) == (
            datetime(2030, 11, 4, 10, 15),
            datetime(2030, 11, 4, 11),
        )

        before = len([r for r in requests if "available-start-times" in r.url.path])
        await driver.click("Back to list")
        legacy_label = next(label for label in _labels(driver) if "Nov 5" in label)
        await driver.click(legacy_label)
        await driver.click("Edit break")
        await driver.click("Change start")
        assert "Enter the break start time" in driver.session.last_message.text
        assert (
            len([r for r in requests if "available-start-times" in r.url.path])
            == before
        )
    finally:
        await _close_bot(driver, http)

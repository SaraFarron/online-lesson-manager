from datetime import UTC, datetime, time, timedelta
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
    await driver.click("Recurring breaks" if weekly else "One-time breaks")
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
    screen_message_id = driver.session.last_message.message_id
    assert "Timezone:" not in driver.session.last_message.text
    assert "Asia/Bangkok" not in driver.session.last_message.text
    start_message_id = driver.update_id + 1
    await driver.text(start_date)
    assert driver.deleted_message_ids()[-1] == start_message_id
    assert driver.session.last_message.message_id == screen_message_id
    await driver.text(start_time)
    await driver.text(end_time)
    if cutoff_date is not None:
        assert "Timezone:" not in driver.session.last_message.text
        assert "Asia/Bangkok" not in driver.session.last_message.text
        cutoff_message_id = driver.update_id + 1
        await driver.text(cutoff_date)
        assert driver.deleted_message_ids()[-1] == cutoff_message_id


def _event_button(driver: BotDriver) -> str:
    assert driver.session.last_message is not None
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    for row in markup.inline_keyboard:
        for button in row:
            if "break ·" in button.text.lower() and button.callback_data is not None:
                return button.text
    raise AssertionError("No break on the current page")


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
        assert "Automatic break reservation (read-only): 5 minutes" in preview_text
        assert "November 5" in preview_text
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
        await driver.click("Save break")
        assert "Affected lessons: 11" in driver.session.last_message.text
        assert "Showing" not in driver.session.last_message.text
        await driver.click("Next ▶")
        assert "Affected lessons: 11" in driver.session.last_message.text
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
        await driver.text("04/11/2030")
        await driver.text("10:00")
        await driver.text("10:30")
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


async def test_weekly_break_edit_preserves_interval_and_delete_requires_confirmation(
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
        await driver.text("05/11/2030")
        await driver.text("11:00")
        await driver.text("11:30")
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
        assert "Delete the entire recurring break?" in driver.session.last_message.text
        assert await db_session.get(RecurrentEvent, existing.id) is not None
        await driver.click("Confirm delete")
        exists = await db_session.execute(
            select(RecurrentEvent.id).where(RecurrentEvent.id == existing.id)
        )
        assert exists.scalar_one_or_none() is None
        assert "Break deleted." in driver.session.last_message.text
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


async def test_deleting_break_requires_confirmation_and_does_not_restore_lessons(
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
        assert "will remain deleted or cancelled" in driver.session.last_message.text
        assert await db_session.get(Event, break_event.id) is not None
        assert (
            await db_session.execute(
                select(RecurrentCancels).where(
                    RecurrentCancels.recurrent_event_id == series.id,
                    RecurrentCancels.start == datetime(2030, 11, 4, 10),
                )
            )
        ).scalar_one_or_none() is not None
        await driver.click("Confirm delete")
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

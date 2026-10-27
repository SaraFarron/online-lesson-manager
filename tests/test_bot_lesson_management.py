from datetime import UTC, datetime, time, timedelta
from itertools import count
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.main import app
from backend.schedule.router import get_schedule_clock
from telegram.api import BackendClient
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

_TELEGRAM_IDS = count(5_300_000_000, 2)
_NOW = datetime(2026, 10, 20, 8, tzinfo=UTC)


async def _seed_accounts(session: AsyncSession) -> tuple[User, User]:
    teacher_telegram_id = next(_TELEGRAM_IDS)
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
        timezone="UTC",
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
                code="t_MANAGE",
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


def _one_time(
    teacher: User,
    student: User,
    start: datetime = datetime(2026, 11, 2, 10),
) -> Event:
    return Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=start,
        end=start.replace(hour=start.hour + 1),
        post_break_minutes=5,
    )


def _series(teacher: User, student: User) -> RecurrentEvent:
    return RecurrentEvent(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 11, 2, 10),
        end=datetime(2026, 11, 2, 11),
        interval=7,
        interval_end=datetime(2026, 12, 1, 10),
        post_break_minutes=5,
        recurrence_mode="TEACHER_LOCAL",
        recurrence_timezone="UTC",
    )


async def _make_bot(
    client: httpx.AsyncClient,
    bot_service_key: str,
    telegram_id: int,
    *,
    lose_update_response: bool = False,
) -> tuple[BotDriver, httpx.AsyncClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    lost_response = False

    async def forward(request: httpx.Request) -> httpx.Response:
        nonlocal lost_response
        requests.append(request)
        response = await client.send(request)
        if (
            lose_update_response
            and not lost_response
            and request.method == "PUT"
            and request.url.path.startswith("/api/v1/lessons/")
        ):
            lost_response = True
            raise httpx.ReadTimeout("The update response was lost")
        return response

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(forward), base_url="http://test"
    )
    api = BackendClient(http, bot_service_key)
    driver = BotDriver(build_dispatcher(api), user_id=telegram_id)
    return driver, http, requests


async def _close_bot(driver: BotDriver, http: httpx.AsyncClient) -> None:
    await driver.close()
    await http.aclose()


def _telegram_id(user: User) -> int:
    if user.telegram_user_id is None:
        raise AssertionError("Seeded account must have a Telegram ID")
    return user.telegram_user_id


def _event_button(driver: BotDriver, subject: str = "Alice Learner") -> str:
    assert driver.session.last_message is not None
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    for row in markup.inline_keyboard:
        for button in row:
            if subject in button.text and button.callback_data is not None:
                return button.text
    raise AssertionError(f"No event button for {subject!r}")


async def _open_list(driver: BotDriver, *, weekly: bool = False) -> None:
    await driver.text("/start")
    await driver.click("Manage events")
    await driver.click("Weekly lessons" if weekly else "One-time lessons")


def _freeze_schedule_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(app.dependency_overrides, get_schedule_clock, lambda: _NOW)


async def test_teacher_edits_one_time_lesson_after_fresh_detail_read(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, student = await _seed_accounts(db_session)
    event = _one_time(teacher, student)
    db_session.add(event)
    await db_session.commit()
    _freeze_schedule_clock(monkeypatch)
    driver, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_list(driver)
        await driver.click(_event_button(driver))
        assert "Alice Learner" in driver.session.last_message.text
        await driver.click("Edit lesson or series")
        await driver.text("03/11/2026")
        await driver.text("11:00")
        assert "Start: Tue, 2026-11-03 11:00" in driver.session.last_message.text
        assert "Automatic break after this lesson (5 min)" in (
            driver.session.last_message.text
        )
        assert "Tue, 2026-11-03 12:00" in driver.session.last_message.text
        await driver.click("Save change")

        await db_session.refresh(event)
        assert event.start == datetime(2026, 11, 3, 11)
        assert event.end == datetime(2026, 11, 3, 12)
        assert event.post_break_minutes == 5
        assert "Automatic break after this lesson (5 min)" in (
            driver.session.last_message.text
        )
        assert "Tue, 2026-11-03 12:00" in driver.session.last_message.text
        update = next(
            request
            for request in requests
            if request.method == "PUT" and request.url.path.endswith(str(event.id))
        )
        assert update.headers.get("if-match")
        assert "Lesson updated." in driver.session.last_message.text
    finally:
        await _close_bot(driver, http)


async def test_student_management_lists_only_own_hidden_lesson(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    own_lesson = _one_time(teacher, student)
    teacher_lesson = Event(
        teacher_id=teacher.id,
        user_id=teacher.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 11, 4, 10),
        end=datetime(2026, 11, 4, 11),
    )
    vacation = Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.VACATION.value,
        start=datetime(2026, 11, 2, 10),
        end=datetime(2026, 11, 2, 11),
    )
    db_session.add_all([own_lesson, teacher_lesson, vacation])
    await db_session.commit()
    driver, http, _ = await _make_bot(client, bot_service_key, _telegram_id(student))
    try:
        await _open_list(driver)
        assert "Alice Learner" in _event_button(driver)
        assert "hidden by vacation" in _event_button(driver)
        assert "Personal lesson" not in driver.session.last_message.text
        await driver.click(_event_button(driver))
        assert "hidden by a vacation" in driver.session.last_message.text
        assert driver.callback_data("Edit lesson or series")
    finally:
        await _close_bot(driver, http)


async def test_student_can_move_and_cancel_own_occurrences_with_break_feedback(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, student = await _seed_accounts(db_session)
    series = _series(teacher, student)
    db_session.add(series)
    await db_session.commit()
    _freeze_schedule_clock(monkeypatch)
    driver, http, _ = await _make_bot(client, bot_service_key, _telegram_id(student))
    try:
        await _open_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        await driver.click("Inspect occurrence")
        await driver.text("16/11/2026")
        await driver.text("10:00")
        assert "Automatic break after this occurrence (5 min)" in (
            driver.session.last_message.text
        )

        await driver.click("Move this occurrence")
        await driver.text("16/11/2026")
        await driver.text("12:00")
        assert "Automatic break after the replacement lesson (5 min)" in (
            driver.session.last_message.text
        )
        await driver.click("Move occurrence")
        assert "Automatic break after the replacement lesson (5 min)" in (
            driver.session.last_message.text
        )
        assert "(cancelled)" in driver.session.last_message.text

        await driver.click("Inspect occurrence")
        await driver.text("23/11/2026")
        await driver.text("10:00")
        await driver.click("Cancel this occurrence")
        assert "automatic break reservation will also be removed" in (
            driver.session.last_message.text
        )
        await driver.click("Confirm")

        assert "No automatic break is reserved after this cancelled occurrence." in (
            driver.session.last_message.text
        )
        assert "automatic break reservation was also removed" in (
            driver.session.last_message.text
        )
        cancellations = list(
            (
                await db_session.scalars(
                    select(RecurrentCancels).where(
                        RecurrentCancels.recurrent_event_id == series.id
                    )
                )
            ).all()
        )
        assert {item.start for item in cancellations} == {
            datetime(2026, 11, 16, 10),
            datetime(2026, 11, 23, 10),
        }
    finally:
        await _close_bot(driver, http)


async def test_deleting_one_time_lesson_requires_confirmation(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    event = _one_time(teacher, student)
    db_session.add(event)
    await db_session.commit()
    driver, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_list(driver)
        await driver.click(_event_button(driver))
        await driver.click("Delete lesson or series")
        assert "Delete this one-time lesson?" in driver.session.last_message.text
        assert "automatic break reservations will also be removed" in (
            driver.session.last_message.text
        )
        assert await db_session.get(Event, event.id) is not None
        await driver.click("Cancel")
        assert await db_session.get(Event, event.id) is not None
        assert not any(request.method == "DELETE" for request in requests)

        await driver.click("Delete lesson or series")
        await driver.click("Confirm")
        remaining = await db_session.scalar(
            select(Event.id).where(Event.id == event.id)
        )
        assert remaining is None
        assert sum(request.method == "DELETE" for request in requests) == 1
        assert "Lesson deleted." in driver.session.last_message.text
    finally:
        await _close_bot(driver, http)


async def test_deleting_weekly_series_removes_only_after_confirmation(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    series = _series(teacher, student)
    db_session.add(series)
    await db_session.flush()
    cancellation = RecurrentCancels(
        recurrent_event_id=series.id,
        start=datetime(2026, 11, 9, 10),
        end=datetime(2026, 11, 9, 11),
    )
    db_session.add(cancellation)
    await db_session.commit()
    driver, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        await driver.click("Delete lesson or series")
        assert "entire weekly series" in driver.session.last_message.text
        assert await db_session.scalar(
            select(RecurrentEvent.id).where(RecurrentEvent.id == series.id)
        )
        assert not any(request.method == "DELETE" for request in requests)

        await driver.click("Confirm")
        remaining_series = await db_session.scalar(
            select(RecurrentEvent.id).where(RecurrentEvent.id == series.id)
        )
        remaining_cancellation = await db_session.scalar(
            select(RecurrentCancels.id).where(RecurrentCancels.id == cancellation.id)
        )
        assert remaining_series is None
        assert remaining_cancellation is None
        assert sum(request.method == "DELETE" for request in requests) == 1
    finally:
        await _close_bot(driver, http)


async def test_stale_list_selection_reloads_authoritative_detail(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    event = _one_time(teacher, student)
    db_session.add(event)
    await db_session.commit()
    driver, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_list(driver)
        stale_callback = driver.callback_data(_event_button(driver))
        await db_session.delete(event)
        await db_session.commit()
        await driver.click(data=stale_callback)

        assert "This lesson is no longer available" in driver.session.last_message.text
        assert "Edit lesson or series" not in [
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        ]
        assert not any(request.method == "PUT" for request in requests)
    finally:
        await _close_bot(driver, http)


async def test_recurring_occurrence_cancel_preserves_series_and_other_occurrences(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    series = _series(teacher, student)
    db_session.add(series)
    await db_session.flush()
    existing_cancel = RecurrentCancels(
        recurrent_event_id=series.id,
        start=datetime(2026, 11, 9, 10),
        end=datetime(2026, 11, 9, 11),
    )
    db_session.add(existing_cancel)
    await db_session.commit()
    driver, http, _ = await _make_bot(client, bot_service_key, _telegram_id(teacher))
    try:
        await _open_list(driver, weekly=True)
        assert "1 cancelled" in _event_button(driver)
        await driver.click(_event_button(driver))

        cancelled_label = next(
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
            if button.text.startswith("Cancelled ·")
        )
        await driver.click(cancelled_label)
        assert "cancelled)" in driver.session.last_message.text
        assert "Cancel this occurrence" not in [
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        ]
        await driver.click("Clear occurrence selection")

        await driver.click("Inspect occurrence")
        await driver.text("16/11/2026")
        await driver.text("10:00")
        assert "(visible)" in driver.session.last_message.text
        await driver.click("Cancel this occurrence")
        assert "Cancel only this weekly occurrence?" in driver.session.last_message.text
        assert "automatic break reservation will also be removed" in (
            driver.session.last_message.text
        )
        assert (
            len(
                list(
                    (
                        await db_session.scalars(
                            select(RecurrentCancels).where(
                                RecurrentCancels.recurrent_event_id == series.id
                            )
                        )
                    ).all()
                )
            )
            == 1
        )
        await driver.click("Confirm")

        await db_session.refresh(series)
        cancellations = list(
            (
                await db_session.scalars(
                    select(RecurrentCancels).where(
                        RecurrentCancels.recurrent_event_id == series.id
                    )
                )
            ).all()
        )
        assert series.interval == 7
        assert len(cancellations) == 2
        assert any(item.start == datetime(2026, 11, 16, 10) for item in cancellations)
        assert "This occurrence was cancelled." in driver.session.last_message.text
        assert "automatic break reservation was also removed" in (
            driver.session.last_message.text
        )
    finally:
        await _close_bot(driver, http)


async def test_cancelled_occurrence_selector_is_paged(
    client, db_session, bot_service_key
) -> None:
    teacher, student = await _seed_accounts(db_session)
    series = _series(teacher, student)
    series.interval_end = datetime(2027, 2, 1, 10)
    db_session.add(series)
    await db_session.flush()
    db_session.add_all(
        RecurrentCancels(
            recurrent_event_id=series.id,
            start=datetime(2026, 11, 9, 10) + timedelta(days=7 * index),
            end=datetime(2026, 11, 9, 11) + timedelta(days=7 * index),
        )
        for index in range(11)
    )
    await db_session.commit()
    driver, http, _ = await _make_bot(client, bot_service_key, _telegram_id(teacher))
    try:
        await _open_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        assert "Cancelled occurrences 1–10 of 11" in driver.session.last_message.text
        assert driver.callback_data("More cancellations ▶")
        await driver.click("More cancellations ▶")
        assert "Cancelled occurrences 11–11 of 11" in driver.session.last_message.text
        last_cancelled = next(
            button.text
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
            if button.text.startswith("Cancelled ·")
        )
        await driver.click(last_cancelled)
        assert "Mon, 2027-01-18 10:00" in driver.session.last_message.text
        assert "(cancelled)" in driver.session.last_message.text
    finally:
        await _close_bot(driver, http)


async def test_recurring_occurrence_move_cancels_exact_source_and_creates_one_off(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, student = await _seed_accounts(db_session)
    series = _series(teacher, student)
    db_session.add(series)
    await db_session.commit()
    _freeze_schedule_clock(monkeypatch)
    driver, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        await driver.click("Inspect occurrence")
        await driver.text("16/11/2026")
        await driver.text("10:00")
        await driver.click("Move this occurrence")
        await driver.text("17/11/2026")
        await driver.text("12:00")
        assert "The original weekly occurrence will be cancelled" in (
            driver.session.last_message.text
        )
        assert "Automatic break after the replacement lesson (5 min)" in (
            driver.session.last_message.text
        )
        assert not list((await db_session.scalars(select(RecurrentCancels))).all())
        await driver.click("Move occurrence")

        cancellation = await db_session.scalar(
            select(RecurrentCancels).where(
                RecurrentCancels.recurrent_event_id == series.id
            )
        )
        assert cancellation is not None
        assert cancellation.start == datetime(2026, 11, 16, 10)
        replacement = await db_session.scalar(
            select(Event).where(
                Event.teacher_id == teacher.id,
                Event.user_id == student.id,
                Event.start == datetime(2026, 11, 17, 12),
            )
        )
        assert replacement is not None
        assert replacement.end == datetime(2026, 11, 17, 13)
        assert "replacement is a one-time lesson" in driver.session.last_message.text
        assert "Automatic break after the replacement lesson (5 min)" in (
            driver.session.last_message.text
        )
        assert (
            sum(
                request.method == "POST"
                and request.url.path.endswith("/occurrences/move")
                for request in requests
            )
            == 1
        )
    finally:
        await _close_bot(driver, http)


async def test_series_edit_warns_and_clears_cancellations_when_recurrence_changes(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, student = await _seed_accounts(db_session)
    series = _series(teacher, student)
    db_session.add(series)
    await db_session.flush()
    db_session.add(
        RecurrentCancels(
            recurrent_event_id=series.id,
            start=datetime(2026, 11, 9, 10),
            end=datetime(2026, 11, 9, 11),
        )
    )
    await db_session.commit()
    _freeze_schedule_clock(monkeypatch)
    driver, http, _ = await _make_bot(client, bot_service_key, _telegram_id(teacher))
    try:
        await _open_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        await driver.click("Edit lesson or series")
        await driver.text("02/11/2026")
        await driver.text("11:30")
        await driver.text("30/11/2026")
        assert "clears saved individual cancellations" in (
            driver.session.last_message.text
        )
        assert (
            len(list((await db_session.scalars(select(RecurrentCancels))).all())) == 1
        )
        await driver.click("Save change")

        await db_session.refresh(series)
        assert series.start == datetime(2026, 11, 2, 11, 30)
        assert series.end == datetime(2026, 11, 2, 12, 30)
        assert series.interval == 7
        assert series.recurrence_mode == "TEACHER_LOCAL"
        assert series.recurrence_timezone == "UTC"
        assert not list((await db_session.scalars(select(RecurrentCancels))).all())
        assert "Lesson updated." in driver.session.last_message.text
    finally:
        await _close_bot(driver, http)


async def test_stale_revision_requires_reload_before_edit_can_submit(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, student = await _seed_accounts(db_session)
    event = _one_time(teacher, student)
    db_session.add(event)
    await db_session.commit()
    _freeze_schedule_clock(monkeypatch)
    driver, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_list(driver)
        await driver.click(_event_button(driver))
        await driver.click("Edit lesson or series")
        await driver.text("03/11/2026")
        await driver.text("11:00")

        event.start = datetime(2026, 11, 2, 10, 30)
        event.end = datetime(2026, 11, 2, 11, 30)
        await db_session.commit()
        await driver.click("Save change")

        assert "Reload it before editing" in driver.session.last_message.text
        assert driver.callback_data("Reload event")
        assert not any(
            button.text == "Save change"
            for row in driver.session.last_message.reply_markup.inline_keyboard
            for button in row
        )
        await driver.click("Reload event")
        assert "Mon, 2026-11-02 10:30" in driver.session.last_message.text
        assert event.start == datetime(2026, 11, 2, 10, 30)
        assert sum(request.method == "PUT" for request in requests) == 1
    finally:
        await _close_bot(driver, http)


async def test_unknown_update_outcome_requires_reload_without_replay(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, student = await _seed_accounts(db_session)
    event = _one_time(teacher, student)
    db_session.add(event)
    await db_session.commit()
    _freeze_schedule_clock(monkeypatch)
    driver, http, requests = await _make_bot(
        client,
        bot_service_key,
        _telegram_id(teacher),
        lose_update_response=True,
    )
    try:
        await _open_list(driver)
        await driver.click(_event_button(driver))
        await driver.click("Edit lesson or series")
        await driver.text("03/11/2026")
        await driver.text("11:00")
        await driver.click("Save change")

        assert "outcome is unknown" in driver.session.last_message.text
        assert driver.callback_data("Reload event")
        await db_session.refresh(event)
        assert event.start == datetime(2026, 11, 3, 11)
        await driver.click("Reload event")
        assert "no write was repeated" in driver.session.last_message.text
        assert "Tue, 2026-11-03 11:00" in driver.session.last_message.text
        assert sum(request.method == "PUT" for request in requests) == 1
    finally:
        await _close_bot(driver, http)


async def test_failed_occurrence_move_keeps_series_and_cancellation_unchanged(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, student = await _seed_accounts(db_session)
    series = _series(teacher, student)
    conflicting_event = Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 11, 17, 12),
        end=datetime(2026, 11, 17, 13),
    )
    db_session.add_all([series, conflicting_event])
    await db_session.commit()
    _freeze_schedule_clock(monkeypatch)
    driver, http, requests = await _make_bot(
        client, bot_service_key, _telegram_id(teacher)
    )
    try:
        await _open_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        await driver.click("Inspect occurrence")
        await driver.text("16/11/2026")
        await driver.text("10:00")
        await driver.click("Move this occurrence")
        await driver.text("17/11/2026")
        await driver.text("12:00")
        await driver.click("Move occurrence")

        assert "That time overlaps another scheduled event." in (
            driver.session.last_message.text
        )
        assert not list((await db_session.scalars(select(RecurrentCancels))).all())
        assert await db_session.get(Event, conflicting_event.id) is not None
        assert (
            sum(
                request.method == "POST"
                and request.url.path.endswith("/occurrences/move")
                for request in requests
            )
            == 1
        )
    finally:
        await _close_bot(driver, http)


async def test_automatic_break_conflict_is_actionable_when_moving_occurrence(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    teacher, student = await _seed_accounts(db_session)
    series = _series(teacher, student)
    earlier_lesson = Event(
        teacher_id=teacher.id,
        user_id=student.id,
        event_type=EventType.LESSON.value,
        start=datetime(2026, 11, 16, 9),
        end=datetime(2026, 11, 16, 10),
        post_break_minutes=5,
    )
    db_session.add_all([series, earlier_lesson])
    await db_session.commit()
    _freeze_schedule_clock(monkeypatch)
    driver, http, _ = await _make_bot(client, bot_service_key, _telegram_id(teacher))
    try:
        await _open_list(driver, weekly=True)
        await driver.click(_event_button(driver))
        await driver.click("Inspect occurrence")
        await driver.text("16/11/2026")
        await driver.text("10:00")
        await driver.click("Move this occurrence")
        await driver.text("16/11/2026")
        await driver.text("10:02")
        await driver.click("Move occurrence")

        assert (
            "That time overlaps an automatic break reserved after a lesson."
            in driver.session.last_message.text
        )
        assert "Choose another time." in driver.session.last_message.text
        assert not list((await db_session.scalars(select(RecurrentCancels))).all())
        assert await db_session.get(Event, earlier_lesson.id) is not None
    finally:
        await _close_bot(driver, http)

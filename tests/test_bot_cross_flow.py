from datetime import UTC, date, datetime, time
from itertools import count
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.constants import EventType
from backend.events.models import Event
from backend.main import app
from backend.schedule.router import get_schedule_clock
from telegram.api import BackendClient
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

_TELEGRAM_IDS = count(5_200_000_000, 10)
_LOCAL_DATE = "04/11/2030"
_SCHEDULE_DATE = date(2030, 11, 4)
_STUDENT_DATE = date(2030, 11, 3)
_NOW = datetime(2030, 11, 4, 0, tzinfo=UTC)


async def _seed_accounts(
    session: AsyncSession,
) -> tuple[User, User, User, User, User]:
    teacher_telegram_id = next(_TELEGRAM_IDS)
    teacher = User(
        id=uuid4(),
        full_name="Cross-flow Teacher",
        role="TEACHER",
        timezone="Asia/Bangkok",
        telegram_user_id=teacher_telegram_id,
        telegram_private_chat_id=teacher_telegram_id,
        is_active=True,
    )
    alice = User(
        id=uuid4(),
        full_name="Alice Learner",
        role="STUDENT",
        timezone="America/Los_Angeles",
        teacher_id=teacher.id,
        telegram_user_id=teacher_telegram_id + 1,
        telegram_private_chat_id=teacher_telegram_id + 1,
        is_active=True,
    )
    bob = User(
        id=uuid4(),
        full_name="Bob Learner",
        role="STUDENT",
        timezone="America/Los_Angeles",
        teacher_id=teacher.id,
        telegram_user_id=teacher_telegram_id + 2,
        telegram_private_chat_id=teacher_telegram_id + 2,
        is_active=True,
    )
    other_teacher = User(
        id=uuid4(),
        full_name="Unrelated Teacher",
        role="TEACHER",
        timezone="UTC",
        telegram_user_id=teacher_telegram_id + 3,
        telegram_private_chat_id=teacher_telegram_id + 3,
        is_active=True,
    )
    outsider = User(
        id=uuid4(),
        full_name="Unrelated Student",
        role="STUDENT",
        timezone="UTC",
        teacher_id=other_teacher.id,
        telegram_user_id=teacher_telegram_id + 4,
        telegram_private_chat_id=teacher_telegram_id + 4,
        is_active=True,
    )
    session.add_all(
        [
            teacher,
            alice,
            bob,
            other_teacher,
            outsider,
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
            TeacherProfile(
                teacher_id=other_teacher.id,
                code=f"t_{(teacher_telegram_id + 3) % 1_000_000:06d}",
                work_start=time(9),
                work_end=time(18),
                working_days=[1, 2, 3, 4, 5],
                lesson_length=60,
                between_lessons_break=5,
                max_consecutive_lessons=4,
                max_lessons_per_day=6,
            ),
            StudentProfile(student_id=alice.id),
            StudentProfile(student_id=bob.id),
            StudentProfile(student_id=outsider.id),
        ]
    )
    await session.commit()
    return teacher, alice, bob, other_teacher, outsider


async def _book_one_time(driver: BotDriver, student: str, start_time: str) -> None:
    await driver.click("Add lesson")
    await driver.click(student)
    await driver.click("One time")
    await driver.click("Use default (60 min)")
    await driver.text(_LOCAL_DATE)
    await driver.text(start_time)
    await driver.click("Create lesson")
    await driver.click("Back to home")


async def _create_student_vacation(driver: BotDriver) -> None:
    await driver.click("Add vacation")
    await driver.click("Alice Learner")
    await driver.text(_LOCAL_DATE)
    await driver.text("09:00")
    await driver.text(_LOCAL_DATE)
    await driver.click("Save vacation")


async def _add_break(driver: BotDriver) -> None:
    await driver.click("Add break")
    await driver.text(_LOCAL_DATE)
    await driver.text("09:30")
    await driver.text("12:30")
    await driver.click("Save break")


def _items(schedule) -> list:
    return [item for day in schedule.days for item in day.items]


def _assert_single_edited_message(driver: BotDriver) -> None:
    sent = [
        call for call in driver.session.calls if call.__api_method__ == "sendMessage"
    ]
    edited = [
        call
        for call in driver.session.calls
        if call.__api_method__ == "editMessageText"
    ]
    assert driver.session.last_message is not None
    assert len(sent) == 1
    assert edited
    assert {call.model_dump().get("message_id") for call in edited} == {
        driver.session.last_message.message_id
    }


async def test_teacher_two_students_and_unrelated_teacher_cross_flow(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    monkeypatch,
) -> None:
    monkeypatch.setitem(app.dependency_overrides, get_schedule_clock, lambda: _NOW)
    teacher, alice, bob, other_teacher, outsider = await _seed_accounts(db_session)
    teacher_id = teacher.telegram_user_id
    alice_id = alice.telegram_user_id
    bob_id = bob.telegram_user_id
    other_teacher_id = other_teacher.telegram_user_id
    assert teacher_id is not None
    assert alice_id is not None
    assert bob_id is not None
    assert other_teacher_id is not None
    unrelated_lesson = Event(
        teacher_id=other_teacher.id,
        user_id=outsider.id,
        event_type=EventType.LESSON.value,
        start=datetime(2030, 11, 4, 3),
        end=datetime(2030, 11, 4, 4),
    )
    db_session.add(unrelated_lesson)
    await db_session.commit()

    requests: list[httpx.Request] = []

    async def forward(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return await client.send(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(forward), base_url="http://test"
    ) as http:
        api = BackendClient(http, bot_service_key)
        teacher_bot = BotDriver(build_dispatcher(api), user_id=teacher_id)
        alice_bot = BotDriver(build_dispatcher(api), user_id=alice_id)
        bob_bot = BotDriver(build_dispatcher(api), user_id=bob_id)
        other_bot = BotDriver(build_dispatcher(api), user_id=other_teacher_id)
        restarted_alice: BotDriver | None = None
        alice_closed = False
        try:
            for driver in (teacher_bot, alice_bot, bob_bot, other_bot):
                await driver.text("/start")
            stale_settings = alice_bot.callback_data("Settings")

            await teacher_bot.click("Settings")
            await teacher_bot.click("Account settings")
            await teacher_bot.click("Change daily digest")
            await teacher_bot.text("06:45")
            await teacher_bot.click("Save account settings")
            await teacher_bot.click("Teacher settings")
            await teacher_bot.click("Between-lesson break")
            await teacher_bot.text("0")
            await teacher_bot.click("Consecutive lesson limit")
            await teacher_bot.text("2")
            await teacher_bot.click("Lessons per day")
            await teacher_bot.text("2")
            await teacher_bot.click("Save teacher settings")
            await teacher_bot.click("Back to home")

            saved_teacher = await api.view(teacher_id, teacher_id)
            assert saved_teacher.account.notification_lessons_today == 405
            assert saved_teacher.teacher.between_lessons_break == 0
            assert saved_teacher.teacher.max_consecutive_lessons == 2
            assert saved_teacher.teacher.max_lessons_per_day == 2

            await _book_one_time(teacher_bot, "Alice Learner", "10:00")
            await _book_one_time(teacher_bot, "Bob Learner", "11:00")
            lessons = list(
                (
                    await db_session.scalars(
                        select(Event)
                        .where(
                            Event.teacher_id == teacher.id,
                            Event.event_type == EventType.LESSON.value,
                        )
                        .order_by(Event.start)
                    )
                ).all()
            )
            assert [
                (lesson.user_id, lesson.start, lesson.end) for lesson in lessons
            ] == [
                (alice.id, datetime(2030, 11, 4, 3), datetime(2030, 11, 4, 4)),
                (bob.id, datetime(2030, 11, 4, 4), datetime(2030, 11, 4, 5)),
            ]

            await teacher_bot.click("Add lesson")
            await teacher_bot.click("Alice Learner")
            await teacher_bot.click("One time")
            await teacher_bot.click("Use default (60 min)")
            await teacher_bot.text(_LOCAL_DATE)
            await teacher_bot.text("12:00")
            await teacher_bot.click("Create lesson")
            assert "daily lesson limit" in teacher_bot.session.last_message.text
            assert (
                len(
                    list(
                        (
                            await db_session.scalars(
                                select(Event).where(
                                    Event.teacher_id == teacher.id,
                                    Event.event_type == EventType.LESSON.value,
                                )
                            )
                        ).all()
                    )
                )
                == 2
            )
            await teacher_bot.click("Cancel")

            await teacher_bot.click("Today")
            teacher_today = teacher_bot.session.last_message.text
            assert "Alice Learner" in teacher_today
            assert "Bob Learner" in teacher_today
            assert "Unrelated" not in teacher_today
            await teacher_bot.click("Back to home")

            for student_bot in (alice_bot, bob_bot):
                await student_bot.click("Today")
                assert "Unrelated" not in student_bot.session.last_message.text
                await student_bot.click("Back to home")
            await other_bot.click("Today")
            assert "Unrelated Student" in other_bot.session.last_message.text
            assert "Alice Learner" not in other_bot.session.last_message.text
            await other_bot.click("Back to home")

            await teacher_bot.click("Vacations")
            await _create_student_vacation(teacher_bot)
            vacation = (
                await db_session.scalars(
                    select(Event).where(
                        Event.teacher_id == teacher.id,
                        Event.event_type == EventType.VACATION.value,
                    )
                )
            ).one()
            assert vacation.user_id == alice.id
            assert vacation.start == datetime(2030, 11, 4, 2)
            assert vacation.end == datetime(2030, 11, 4, 17)
            alice_events = _items(
                await api.read_schedule_day(
                    alice_id,
                    alice_id,
                    _STUDENT_DATE,
                )
            )
            bob_events = _items(
                await api.read_schedule_day(
                    bob_id,
                    bob_id,
                    _STUDENT_DATE,
                )
            )
            assert lessons[0].id not in {item.source_id for item in alice_events}
            assert {item.source_id for item in bob_events} == {lessons[1].id}
            vacation_button = next(
                button.text
                for row in teacher_bot.session.last_message.reply_markup.inline_keyboard
                for button in row
                if "Alice Learner" in button.text
            )
            await teacher_bot.click(vacation_button)
            await teacher_bot.click("Delete vacation")
            await teacher_bot.click("Confirm delete")
            assert (
                await db_session.scalar(select(Event.id).where(Event.id == vacation.id))
                is None
            )
            alice_events = _items(
                await api.read_schedule_day(
                    alice.telegram_user_id,
                    alice.telegram_private_chat_id,
                    _STUDENT_DATE,
                )
            )
            assert lessons[0].id in {item.source_id for item in alice_events}
            await teacher_bot.click("Back")
            await teacher_bot.click("Back to home")

            await teacher_bot.click("Breaks")
            await teacher_bot.click("One-time breaks")
            await _add_break(teacher_bot)
            assert teacher_bot.session.last_message.text.count("One-time lesson") == 2
            lesson_ids = {lesson.id for lesson in lessons}
            stored_ids = set(
                (
                    await db_session.scalars(
                        select(Event.id).where(Event.id.in_(lesson_ids))
                    )
                ).all()
            )
            assert stored_ids == lesson_ids
            assert (
                sum(
                    request.method == "POST" and request.url.path == "/api/v1/breaks/"
                    for request in requests
                )
                == 1
            )
            await teacher_bot.click("Cancel; make no changes")
            stored_ids = set(
                (
                    await db_session.scalars(
                        select(Event.id).where(Event.id.in_(lesson_ids))
                    )
                ).all()
            )
            assert stored_ids == lesson_ids
            await teacher_bot.click("Cancel")

            await _add_break(teacher_bot)
            await teacher_bot.click("Confirm and displace lessons")
            remaining_ids = set(
                (
                    await db_session.scalars(
                        select(Event.id).where(Event.id.in_(lesson_ids))
                    )
                ).all()
            )
            assert remaining_ids == set()
            assert (
                sum(
                    request.method == "POST" and request.url.path == "/api/v1/breaks/"
                    for request in requests
                )
                == 3
            )
            break_event = (
                await db_session.scalars(
                    select(Event).where(
                        Event.teacher_id == teacher.id,
                        Event.event_type == EventType.BREAK.value,
                    )
                )
            ).one()
            assert break_event.user_id == teacher.id
            assert break_event.start == datetime(2030, 11, 4, 2, 30)
            assert break_event.end == datetime(2030, 11, 4, 5, 30)
            assert await db_session.get(Event, unrelated_lesson.id) is not None

            for student_bot, student in ((alice_bot, alice), (bob_bot, bob)):
                schedule = _items(
                    await api.read_schedule_day(
                        alice_id if student.id == alice.id else bob_id,
                        alice_id if student.id == alice.id else bob_id,
                        _STUDENT_DATE,
                    )
                )
                assert schedule == []
                assert "Breaks" not in student_bot.session.last_message.text
            other_schedule = _items(
                await api.read_schedule_day(
                    other_teacher_id,
                    other_teacher_id,
                    _SCHEDULE_DATE,
                )
            )
            assert {item.source_id for item in other_schedule} == {unrelated_lesson.id}

            for driver in (teacher_bot, alice_bot, bob_bot, other_bot):
                _assert_single_edited_message(driver)

            await alice_bot.close()
            alice_closed = True
            restarted_alice = BotDriver(
                build_dispatcher(BackendClient(http, bot_service_key)),
                user_id=alice_id,
            )
            await restarted_alice.text("/start")
            assert "STUDENT" in restarted_alice.session.last_message.text
            request_count = len(requests)
            await restarted_alice.click(data=stale_settings)
            assert (
                restarted_alice.session.calls[-1].__api_method__
                == "answerCallbackQuery"
            )
            assert len(requests) == request_count
        finally:
            await teacher_bot.close()
            if not alice_closed:
                await alice_bot.close()
            await bob_bot.close()
            await other_bot.close()
            if restarted_alice is not None:
                await restarted_alice.close()

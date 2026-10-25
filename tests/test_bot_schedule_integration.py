from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from uuid import UUID, uuid4

import httpx
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import StudentProfile, TeacherProfile, User
from backend.auth.utils import create_access_token
from backend.events.constants import RecurrenceMode
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.main import app
from backend.schedule.router import get_schedule_clock
from telegram.api import BackendClient, ScheduleResponse
from telegram.runtime import build_dispatcher
from telegram.schedule_format import MAX_LABEL_CODEPOINTS, MAX_MESSAGE_UTF16_UNITS
from tests.bot_support import BotDriver

TEACHER_TG_ID = 5_100_000_000
ALICE_TG_ID = 5_100_000_001
BOB_TG_ID = 5_100_000_002
OTHER_TEACHER_TG_ID = 5_100_000_003


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self, markup: str) -> str:
        self.feed(markup)
        return "".join(self.parts)


def _user(
    role: str,
    full_name: str,
    timezone: str,
    *,
    telegram_id: int | None = None,
    teacher_id: UUID | None = None,
) -> User:
    return User(
        id=uuid4(),
        email=f"{uuid4()}@example.com",
        full_name=full_name,
        role=role,
        timezone=timezone,
        teacher_id=teacher_id,
        telegram_user_id=telegram_id,
        telegram_private_chat_id=telegram_id,
        is_active=True,
    )


async def _seed_schedule_accounts(
    session: AsyncSession,
) -> tuple[User, User, User, User, User]:
    teacher = _user(
        "TEACHER",
        "Teacher Owner",
        "Asia/Bangkok",
        telegram_id=TEACHER_TG_ID,
    )
    alice = _user(
        "STUDENT",
        "Alice Learner",
        "America/Los_Angeles",
        telegram_id=ALICE_TG_ID,
        teacher_id=teacher.id,
    )
    bob = _user(
        "STUDENT",
        "Bob Learner",
        "America/Los_Angeles",
        telegram_id=BOB_TG_ID,
        teacher_id=teacher.id,
    )
    other_teacher = _user(
        "TEACHER",
        "Unrelated Teacher",
        "UTC",
        telegram_id=OTHER_TEACHER_TG_ID,
    )
    outsider = _user(
        "STUDENT",
        "Unrelated Student",
        "UTC",
        teacher_id=other_teacher.id,
    )
    session.add_all([teacher, alice, bob, other_teacher, outsider])
    await session.flush()
    session.add_all(
        [
            TeacherProfile(teacher_id=teacher.id, code="t_INTEG01"),
            TeacherProfile(teacher_id=other_teacher.id, code="t_INTEG02"),
            StudentProfile(student_id=alice.id),
            StudentProfile(student_id=bob.id),
            StudentProfile(student_id=outsider.id),
        ]
    )
    await session.commit()
    return teacher, alice, bob, other_teacher, outsider


def _add_event(
    teacher: User,
    subject: User,
    event_type: str,
    start: datetime,
    end: datetime,
) -> Event:
    return Event(
        teacher_id=teacher.id,
        user_id=subject.id,
        event_type=event_type,
        start=start,
        end=end,
    )


async def _raw_schedule(
    client: httpx.AsyncClient,
    token: str,
    path: str,
) -> httpx.Response:
    return await client.get(
        path,
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_authorized_backend_results_match_teacher_and_student_bot_views(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    monkeypatch,
) -> None:
    now = datetime(2026, 10, 18, 18, 30, tzinfo=UTC)
    monkeypatch.setitem(app.dependency_overrides, get_schedule_clock, lambda: now)
    teacher, alice, bob, other_teacher, outsider = await _seed_schedule_accounts(
        db_session
    )

    personal = _add_event(
        teacher,
        teacher,
        "LESSON",
        datetime(2026, 10, 19, 0, 30),
        datetime(2026, 10, 19, 1),
    )
    alice_one_off = _add_event(
        teacher,
        alice,
        "LESSON",
        datetime(2026, 10, 19, 2),
        datetime(2026, 10, 19, 3),
    )
    bob_monday = _add_event(
        teacher,
        bob,
        "LESSON",
        datetime(2026, 10, 19, 3),
        datetime(2026, 10, 19, 4),
    )
    teacher_break = _add_event(
        teacher,
        teacher,
        "BREAK",
        datetime(2026, 10, 19, 11),
        datetime(2026, 10, 19, 11, 30),
    )
    cross_midnight = _add_event(
        teacher,
        bob,
        "LESSON",
        datetime(2026, 10, 19, 16, 30),
        datetime(2026, 10, 19, 18, 30),
    )
    alice_tuesday = _add_event(
        teacher,
        alice,
        "LESSON",
        datetime(2026, 10, 20, 3),
        datetime(2026, 10, 20, 4),
    )
    bob_tuesday = _add_event(
        teacher,
        bob,
        "LESSON",
        datetime(2026, 10, 20, 3),
        datetime(2026, 10, 20, 4),
    )
    bob_during_teacher_vacation = _add_event(
        teacher,
        bob,
        "LESSON",
        datetime(2026, 10, 21, 4, 30),
        datetime(2026, 10, 21, 5, 30),
    )
    unrelated_event = _add_event(
        other_teacher,
        outsider,
        "LESSON",
        datetime(2026, 10, 19, 2),
        datetime(2026, 10, 19, 3),
    )
    alice_student_vacation = _add_event(
        teacher,
        alice,
        "VACATION",
        datetime(2026, 10, 20, 3, 30),
        datetime(2026, 10, 20, 4, 30),
    )
    alice_recurring_vacation = _add_event(
        teacher,
        alice,
        "VACATION",
        datetime(2026, 10, 21, 6, 30),
        datetime(2026, 10, 21, 7, 30),
    )
    teacher_vacation = _add_event(
        teacher,
        teacher,
        "VACATION",
        datetime(2026, 10, 21, 4),
        datetime(2026, 10, 21, 5),
    )
    recurring = RecurrentEvent(
        teacher_id=teacher.id,
        user_id=alice.id,
        event_type="LESSON",
        start=datetime(2026, 10, 19, 6),
        end=datetime(2026, 10, 19, 7),
        interval=1,
        interval_end=datetime(2026, 10, 23, 6),
        recurrence_mode=RecurrenceMode.UTC_STEP.value,
    )
    db_session.add_all(
        [
            personal,
            alice_one_off,
            bob_monday,
            teacher_break,
            cross_midnight,
            alice_tuesday,
            bob_tuesday,
            bob_during_teacher_vacation,
            unrelated_event,
            alice_student_vacation,
            alice_recurring_vacation,
            teacher_vacation,
            recurring,
        ]
    )
    await db_session.flush()
    cancelled_start = datetime(2026, 10, 20, 6)
    db_session.add(
        RecurrentCancels(
            recurrent_event_id=recurring.id,
            start=cancelled_start,
            end=datetime(2026, 10, 20, 7),
        )
    )
    await db_session.commit()

    requests: list[tuple[str, str]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return await client.send(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, bot_service_key)
        teacher_bot = BotDriver(build_dispatcher(api), user_id=TEACHER_TG_ID)
        alice_bot = BotDriver(build_dispatcher(api), user_id=ALICE_TG_ID)
        bob_bot = BotDriver(build_dispatcher(api), user_id=BOB_TG_ID)
        try:
            for driver in (teacher_bot, alice_bot, bob_bot):
                await driver.text("/start")

            requests.clear()
            statements: list[str] = []

            def record_statement(
                _connection,
                _cursor,
                statement: str,
                _parameters,
                _context,
                _executemany,
            ) -> None:
                statements.append(statement)

            engine = db_session.get_bind()
            event.listen(engine, "before_cursor_execute", record_statement)
            try:
                await teacher_bot.click("Today")
                teacher_today = teacher_bot.session.last_message.text
                assert "Monday, October 19, 2026" in teacher_today
                assert "NOW" in teacher_today
                assert "Alice Learner" in teacher_today
                assert "Bob Learner" in teacher_today
                assert "Unrelated" not in teacher_today
                assert teacher_today.count("23:30–01:30 (+1 day)") == 1
                day_select_count = sum(
                    statement.lstrip().upper().startswith("SELECT")
                    for statement in statements
                )
                statements.clear()

                await teacher_bot.click("This week")
                teacher_week_text = teacher_bot.session.last_message.text
                week_select_count = sum(
                    statement.lstrip().upper().startswith("SELECT")
                    for statement in statements
                )
            finally:
                event.remove(engine, "before_cursor_execute", record_statement)

            assert requests == [
                ("GET", "/api/v1/schedule/day"),
                ("GET", "/api/v1/schedule/week"),
            ]
            assert 0 < day_select_count <= 12
            assert 0 < week_select_count <= 12
            assert "This week's schedule" in teacher_week_text
            assert teacher_week_text.count("No events") == 4
            assert "Personal lesson" in teacher_week_text
            assert "Break" in teacher_week_text
            assert "Alice Learner" in teacher_week_text
            assert "Bob Learner" in teacher_week_text
            assert "Unrelated Teacher" not in teacher_week_text
            assert "Unrelated Student" not in teacher_week_text
            assert teacher_week_text.count("23:30–01:30 (+1 day)") == 1
            assert teacher_week_text.index("NOW") < teacher_week_text.index(
                "[UPCOMING]"
            )

            teacher_token = api.tokens[TEACHER_TG_ID]
            teacher_response = await _raw_schedule(
                client,
                teacher_token,
                "/api/v1/schedule/week?date=2026-10-19",
            )
            assert teacher_response.status_code == 200
            teacher_json = teacher_response.json()
            assert set(teacher_json) == {
                "period",
                "timezone",
                "generated_at",
                "range_start",
                "range_end",
                "days",
            }
            assert teacher_json["timezone"] == "Asia/Bangkok"
            assert teacher_json["generated_at"] == "2026-10-18T18:30:00+00:00"
            assert len(teacher_json["days"]) == 7
            teacher_items = [
                item for day in teacher_json["days"] for item in day["items"]
            ]
            expected_ids = {
                str(personal.id),
                str(alice_one_off.id),
                str(bob_monday.id),
                str(teacher_break.id),
                str(cross_midnight.id),
                str(bob_tuesday.id),
                str(recurring.id),
            }
            actual_ids = {item["source_id"] for item in teacher_items}
            assert len(teacher_items) == 8
            assert actual_ids == expected_ids
            recurrent_items = [
                item
                for item in teacher_items
                if item["source_kind"] == "recurrent_event"
            ]
            assert [item["start"] for item in recurrent_items] == [
                "2026-10-19T06:00:00+00:00",
                "2026-10-22T06:00:00+00:00",
            ]
            allowed_item_fields = {
                "source_kind",
                "source_id",
                "event_type",
                "start",
                "end",
                "status",
                "subject_label",
                "parent_kind",
                "parent_start",
            }
            assert all(set(item) <= allowed_item_fields for item in teacher_items)
            assert str(alice_tuesday.id) not in actual_ids
            assert str(bob_during_teacher_vacation.id) not in actual_ids
            assert str(unrelated_event.id) not in actual_ids
            assert str(alice_student_vacation.id) not in actual_ids
            assert str(alice_recurring_vacation.id) not in actual_ids
            assert str(teacher_vacation.id) not in actual_ids
            assert "Unrelated Teacher" not in teacher_response.text
            assert "Unrelated Student" not in teacher_response.text
            assert str(TEACHER_TG_ID) not in teacher_response.text

            personal_lesson = next(
                item for item in teacher_items if item["source_id"] == str(personal.id)
            )
            assert personal_lesson["subject_label"] == "Personal lesson"
            monday = next(
                day for day in teacher_json["days"] if day["date"] == "2026-10-19"
            )
            assert (
                sum(
                    item["source_id"] == str(cross_midnight.id)
                    for item in monday["items"]
                )
                == 1
            )
            assert not next(
                day for day in teacher_json["days"] if day["date"] == "2026-10-21"
            )["items"]
            assert str(cross_midnight.id) not in {
                item["source_id"]
                for item in next(
                    day for day in teacher_json["days"] if day["date"] == "2026-10-20"
                )["items"]
            }

            requests.clear()
            await alice_bot.click("Today")
            alice_text = alice_bot.session.last_message.text
            assert requests == [("GET", "/api/v1/schedule/day")]
            assert "Sunday, October 18, 2026" in alice_text
            assert "19:00–20:00" in alice_text
            assert "23:00–00:00 (+1 day)" in alice_text
            assert "Alice Learner" not in alice_text
            assert "Bob Learner" not in alice_text
            assert "Teacher Owner" not in alice_text
            assert alice_text.index("NOW") < alice_text.index("19:00–20:00")

            requests.clear()
            await bob_bot.click("Today")
            bob_text = bob_bot.session.last_message.text
            assert requests == [("GET", "/api/v1/schedule/day")]
            assert "Sunday, October 18, 2026" in bob_text
            assert "20:00–21:00" in bob_text
            assert "Alice Learner" not in bob_text
            assert "Unrelated Student" not in bob_text

            alice_today = await api.read_schedule_day(ALICE_TG_ID, ALICE_TG_ID)
            bob_today = await api.read_schedule_day(BOB_TG_ID, BOB_TG_ID)
            assert alice_today.days[0].date == date(2026, 10, 18)
            assert bob_today.days[0].date == date(2026, 10, 18)
            assert [item.source_id for item in alice_today.days[0].items] == [
                alice_one_off.id,
                recurring.id,
            ]
            assert [item.source_id for item in bob_today.days[0].items] == [
                bob_monday.id
            ]

            alice_day = await api.read_schedule_day(
                ALICE_TG_ID, ALICE_TG_ID, date(2026, 10, 19)
            )
            bob_day = await api.read_schedule_day(
                BOB_TG_ID, BOB_TG_ID, date(2026, 10, 19)
            )
            assert alice_day.days[0].items == []
            assert {item.source_id for item in bob_day.days[0].items} == {
                bob_tuesday.id,
                cross_midnight.id,
            }
            assert all(
                item.subject_label is None
                for day in (*alice_today.days, *bob_today.days)
                for item in day.items
            )
            student_raw = await _raw_schedule(
                client,
                api.tokens[ALICE_TG_ID],
                "/api/v1/schedule/day",
            )
            assert student_raw.status_code == 200
            assert "subject_label" not in student_raw.text
            assert "Bob Learner" not in student_raw.text
            assert "Teacher Owner" not in student_raw.text
            assert "Unrelated Student" not in student_raw.text
            assert str(ALICE_TG_ID) not in student_raw.text

            unrelated_token = await api.session(
                OTHER_TEACHER_TG_ID, OTHER_TEACHER_TG_ID
            )
            unrelated_response = await api.read_schedule_day(
                OTHER_TEACHER_TG_ID,
                OTHER_TEACHER_TG_ID,
                date(2026, 10, 19),
            )
            assert [item.source_id for item in unrelated_response.days[0].items] == [
                unrelated_event.id
            ]
            assert isinstance(
                ScheduleResponse.model_validate(
                    unrelated_response.model_dump(mode="python")
                ),
                ScheduleResponse,
            )
            assert unrelated_token.access_token not in teacher_week_text

            unauthorized = await client.get("/api/v1/schedule/day")
            admin = _user("ADMIN", "Administrator", "UTC")
            db_session.add(admin)
            await db_session.commit()
            admin_token = create_access_token(str(admin.id))
            forbidden = await _raw_schedule(client, admin_token, "/api/v1/schedule/day")
            assert unauthorized.status_code == 401
            assert forbidden.status_code == 403

            for driver in (teacher_bot, alice_bot, bob_bot):
                assert driver.session.last_message.reply_markup is not None
                for row in driver.session.last_message.reply_markup.inline_keyboard:
                    for button in row:
                        callback_data = button.callback_data or ""
                        assert api.tokens[driver.user_id] not in callback_data
                        assert str(teacher.id) not in callback_data
                        assert str(alice.id) not in callback_data
                        assert str(bob.id) not in callback_data
                assert (
                    sum(
                        call.__api_method__ == "sendMessage"
                        for call in driver.session.calls
                    )
                    == 1
                )
                assert {
                    call.message_id
                    for call in driver.session.calls
                    if call.__api_method__ == "editMessageText"
                } == {driver.session.last_message.message_id}
        finally:
            await teacher_bot.close()
            await alice_bot.close()
            await bob_bot.close()


async def test_dst_week_and_long_schedule_pages_stay_complete_and_bounded(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    bot_service_key: str,
    monkeypatch,
) -> None:
    now = datetime(2026, 11, 1, 5, 15, tzinfo=UTC)
    monkeypatch.setitem(app.dependency_overrides, get_schedule_clock, lambda: now)
    teacher = _user(
        "TEACHER",
        "DST Teacher",
        "America/New_York",
        telegram_id=TEACHER_TG_ID,
    )
    student = _user(
        "STUDENT",
        "Long <&> 😀 " + "x" * 90,
        "America/New_York",
        telegram_id=ALICE_TG_ID,
        teacher_id=teacher.id,
    )
    db_session.add_all([teacher, student])
    await db_session.flush()
    db_session.add_all(
        [
            TeacherProfile(teacher_id=teacher.id, code="t_DST001"),
            StudentProfile(student_id=student.id),
        ]
    )
    past_event = _add_event(
        teacher,
        student,
        "LESSON",
        datetime(2026, 11, 1, 4, 30),
        datetime(2026, 11, 1, 4, 45),
    )
    edt_event = _add_event(
        teacher,
        student,
        "LESSON",
        datetime(2026, 11, 1, 5, 30),
        datetime(2026, 11, 1, 5, 45),
    )
    est_event = _add_event(
        teacher,
        student,
        "LESSON",
        datetime(2026, 11, 1, 6, 30),
        datetime(2026, 11, 1, 6, 45),
    )
    overflow_events = [
        _add_event(
            teacher,
            student,
            "LESSON",
            datetime(2026, 11, 1, 8) + timedelta(minutes=index),
            datetime(2026, 11, 1, 8) + timedelta(minutes=index, seconds=30),
        )
        for index in range(120)
    ]
    db_session.add_all([past_event, edt_event, est_event, *overflow_events])
    await db_session.commit()

    requests: list[tuple[str, str]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return await client.send(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, bot_service_key)
        driver = BotDriver(build_dispatcher(api), user_id=TEACHER_TG_ID)
        try:
            await driver.text("/start")
            requests.clear()
            query_statements: list[str] = []

            def record_statement(
                _connection,
                _cursor,
                statement: str,
                _parameters,
                _context,
                _executemany,
            ) -> None:
                query_statements.append(statement)

            engine = db_session.get_bind()
            event.listen(engine, "before_cursor_execute", record_statement)
            try:
                await driver.click("Today")
            finally:
                event.remove(engine, "before_cursor_execute", record_statement)

            initial_pages = [driver.session.last_message.text]
            while True:
                try:
                    next_page = driver.callback_data("Next ▶")
                except AssertionError:
                    break
                await driver.click(data=next_page)
                initial_pages.append(driver.session.last_message.text)

            assert requests == [("GET", "/api/v1/schedule/day")]
            select_count = sum(
                statement.lstrip().upper().startswith("SELECT")
                for statement in query_statements
            )
            assert 0 < select_count <= 12
            assert len(initial_pages) > 1
            visible_pages = [_VisibleText().text(page) for page in initial_pages]
            assert sum(page.count("[UPCOMING]") for page in visible_pages) == 122
            assert sum(page.count("[PAST]") for page in visible_pages) == 1
            assert visible_pages[0].index("[PAST]") < visible_pages[0].index("NOW")
            assert visible_pages[0].index("NOW") < visible_pages[0].index("[UPCOMING]")
            assert "01:30 EDT (UTC-04:00)" in visible_pages[0]
            assert "01:30 EST (UTC-05:00)" in visible_pages[0]
            assert all(
                len(page.encode("utf-16-le")) // 2 <= MAX_MESSAGE_UTF16_UNITS
                for page in visible_pages
            )
            for index, page in enumerate(visible_pages, start=1):
                assert f"Page {index}/{len(visible_pages)}" in page

            row = next(
                line
                for page in visible_pages
                for line in page.splitlines()
                if "Long <&>" in line
            )
            label = row.split(" — ", maxsplit=1)[1].rsplit("  [", maxsplit=1)[0]
            assert len(label) == MAX_LABEL_CODEPOINTS
            assert "&lt;&amp;" in initial_pages[0]
            assert len(visible_pages) > 1

            week_response = await api.read_schedule_week(TEACHER_TG_ID, TEACHER_TG_ID)
            assert week_response.days[0].date == date(2026, 10, 26)
            assert week_response.days[-1].date == date(2026, 11, 1)
            assert week_response.range_end - week_response.range_start == timedelta(
                hours=169
            )
            assert len(week_response.days) == 7
            assert sum(len(day.items) for day in week_response.days) == 123
            assert sum(not day.items for day in week_response.days) == 6

            requests.clear()
            await driver.click("This week")
            week_pages = [driver.session.last_message.text]
            while True:
                try:
                    next_page = driver.callback_data("Next ▶")
                except AssertionError:
                    break
                await driver.click(data=next_page)
                week_pages.append(driver.session.last_message.text)
            assert requests == [("GET", "/api/v1/schedule/week")]
            assert len(week_pages) > 1
            week_text = [_VisibleText().text(page) for page in week_pages]
            assert sum(page.count("[UPCOMING]") for page in week_text) == 122
            assert sum(page.count("[PAST]") for page in week_text) == 1
            assert sum(page.count("No events") for page in week_text) == 6
            assert all(
                len(page.encode("utf-16-le")) // 2 <= MAX_MESSAGE_UTF16_UNITS
                for page in week_text
            )
            for index, page in enumerate(week_text, start=1):
                assert f"Page {index}/{len(week_text)}" in page

            sends = [
                call
                for call in driver.session.calls
                if call.__api_method__ == "sendMessage"
            ]
            edits = [
                call
                for call in driver.session.calls
                if call.__api_method__ == "editMessageText"
            ]
            assert len(sends) == 1
            assert edits
            assert {call.message_id for call in edits} == {
                driver.session.last_message.message_id
            }
            assert sum(
                call.__api_method__ == "answerCallbackQuery"
                for call in driver.session.calls
            ) == len(initial_pages) + len(week_pages)
        finally:
            await driver.close()

import asyncio
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy import select

from backend.auth.models import User
from backend.events.constants import RecurrenceMode
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.main import app
from backend.schedule.router import get_schedule_clock
from telegram.api import BackendClient
from telegram.runtime import build_dispatcher
from telegram.schedule_dialogs import ScheduleDialogState
from tests.bot_support import BotDriver
from tests.test_bot_onboarding import teacher_defaults


async def _register_teacher(driver: BotDriver, api: BackendClient) -> None:
    await driver.text("/start")
    await teacher_defaults(driver)
    await driver.click("Register")
    assert "TEACHER:" in driver.session.last_message.text
    assert await api.view(driver.user_id, driver.user_id)


def _assert_no_refresh(driver: BotDriver) -> None:
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    assert all(
        button.text != "Refresh" for row in markup.inline_keyboard for button in row
    )


async def test_registered_teacher_can_navigate_and_reopen_in_place(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    api = BackendClient(client, bot_service_key)
    driver = BotDriver(build_dispatcher(api))
    now = [datetime(2026, 10, 19, 23, 59, tzinfo=UTC)]
    monkeypatch.setitem(app.dependency_overrides, get_schedule_clock, lambda: now[0])
    try:
        await _register_teacher(driver, api)
        view = await api.view(driver.user_id, driver.user_id)
        message_id = driver.session.last_message.message_id
        assert "Lesson booking and schedule viewing are available" in (
            driver.session.last_message.text
        )
        assert "Notifications are not active yet" in driver.session.last_message.text

        await driver.click("Today")
        _assert_no_refresh(driver)
        assert "mon, Oct 19" in driver.session.last_message.text
        assert "No events" in driver.session.last_message.text
        assert driver.session.last_message.message_id == message_id

        await driver.click("This week")
        _assert_no_refresh(driver)
        assert "This week's schedule" in driver.session.last_message.text
        assert driver.session.last_message.text.count("No events") == 7
        assert driver.callback_data("Today")

        db_session.add(
            Event(
                teacher_id=view.account.id,
                user_id=view.account.id,
                event_type="LESSON",
                start=datetime(2026, 10, 20, 9),
                end=datetime(2026, 10, 20, 10),
                post_break_minutes=5,
            )
        )
        await db_session.commit()
        now[0] = datetime(2026, 10, 20, 0, 1, tzinfo=UTC)
        await driver.click("Today")
        _assert_no_refresh(driver)
        assert "tue, Oct 20" in driver.session.last_message.text
        assert "Updated tue, Oct 20 at 0:01" in driver.session.last_message.text
        assert "Personal lesson" in driver.session.last_message.text
        assert "10:00–10:05  Automatic break (read-only)  [UPCOMING]" in (
            driver.session.last_message.text
        )
        assert "No events" not in driver.session.last_message.text

        db_session.add(
            Event(
                teacher_id=view.account.id,
                user_id=view.account.id,
                event_type="LESSON",
                start=datetime(2026, 10, 20, 11),
                end=datetime(2026, 10, 20, 12),
            )
        )
        await db_session.commit()
        await driver.click("Back to home")
        assert "TEACHER:" in driver.session.last_message.text
        assert "Lesson booking and schedule viewing are available" in (
            driver.session.last_message.text
        )
        assert driver.session.last_message.message_id == message_id
        await driver.click("Today")
        assert "Today's schedule" in driver.session.last_message.text
        assert "Personal lesson" in driver.session.last_message.text
        assert "Automatic break (read-only)" in driver.session.last_message.text
        assert "11:00–12:00" in driver.session.last_message.text
        _assert_no_refresh(driver)

        sent = [
            call
            for call in driver.session.calls
            if call.__api_method__ == "sendMessage"
        ]
        edits = [
            call
            for call in driver.session.calls
            if call.__api_method__ == "editMessageText"
        ]
        answers = [
            call
            for call in driver.session.calls
            if call.__api_method__ == "answerCallbackQuery"
        ]
        assert len(sent) == 1
        assert edits and {call.message_id for call in edits} == {message_id}
        assert len(answers) >= 6
    finally:
        await driver.close()


async def test_schedule_outage_offers_navigation_recovery_without_stale_rows(
    client, bot_service_key
) -> None:
    offline = [False]

    async def transport(request: httpx.Request) -> httpx.Response:
        if offline[0]:
            raise httpx.ConnectError("test outage", request=request)
        return await client.send(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, bot_service_key)
        driver = BotDriver(build_dispatcher(api))
        try:
            await _register_teacher(driver, api)
            await driver.click("Today")
            assert "No events" in driver.session.last_message.text
            _assert_no_refresh(driver)
            offline[0] = True
            await driver.click("This week")
            error_text = driver.session.last_message.text
            assert "Schedule is unavailable" in error_text
            assert "No events" not in error_text
            assert "return home and reopen" in error_text
            _assert_no_refresh(driver)
            assert driver.callback_data("Back to home")

            offline[0] = False
            await driver.click("Back to home")
            await driver.click("Today")
            assert "Today's schedule" in driver.session.last_message.text
            assert "No events" in driver.session.last_message.text

            await driver.click("This week")
            _assert_no_refresh(driver)
            offline[0] = True
            await driver.click("◀ Previous week")
            error_text = driver.session.last_message.text
            assert "Schedule is unavailable" in error_text
            assert "No events" not in error_text
            assert "return home and reopen" in error_text
            _assert_no_refresh(driver)
            assert driver.callback_data("Back to home")

            offline[0] = False
            await driver.click("Next week ▶")
            assert "This week's schedule" in driver.session.last_message.text
            assert driver.session.last_message.text.count("No events") == 7
            await driver.click("Back to home")
            assert "TEACHER:" in driver.session.last_message.text
        finally:
            await driver.close()


async def test_expired_token_recovers_and_inactive_account_gets_schedule_error(
    client, db_session, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    driver = BotDriver(build_dispatcher(api))
    try:
        await _register_teacher(driver, api)
        await driver.click("Today")
        api.tokens[driver.user_id] = "expired-test-token"
        await driver.click("This week")
        assert "This week's schedule" in driver.session.last_message.text

        account = await db_session.scalar(
            select(User).where(User.telegram_user_id == driver.user_id)
        )
        assert account is not None
        account.is_active = False
        await db_session.commit()
        await driver.click("Today")

        error_text = driver.session.last_message.text
        assert "cannot view a schedule" in error_text
        assert "No events" not in error_text
        assert "return home and reopen" in error_text
        _assert_no_refresh(driver)
        assert driver.callback_data("Back to home")
    finally:
        await driver.close()


async def test_registered_student_can_open_week_and_return_home(
    client, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    teacher = BotDriver(build_dispatcher(api))
    student = BotDriver(build_dispatcher(api), user_id=5000000001)
    try:
        await _register_teacher(teacher, api)
        invitation = teacher.session.last_message.text.split("Student invitation: ")[1]
        payload = parse_qs(urlsplit(invitation).query)["start"][0]
        await student.text("/start " + payload)
        await student.click("Confirm teacher")
        for label in [
            "Use current/default",
            "Use current/default",
            "Use current minutes/off",
            "Use current/default",
            "Register",
        ]:
            await student.click(label)

        message_id = student.session.last_message.message_id
        assert "STUDENT:" in student.session.last_message.text
        assert "Lesson booking and schedule viewing are available" in (
            student.session.last_message.text
        )
        await student.click("Today")
        _assert_no_refresh(student)
        await student.click("This week")
        assert "This week's schedule" in student.session.last_message.text
        assert student.session.last_message.text.count("No events") == 7
        await student.click("Back to home")
        assert "STUDENT:" in student.session.last_message.text
        assert student.session.last_message.message_id == message_id
        assert (
            sum(call.__api_method__ == "sendMessage" for call in student.session.calls)
            == 1
        )
    finally:
        await teacher.close()
        await student.close()


async def test_teacher_and_student_can_navigate_selected_week_with_fresh_data(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    requests: list[str | None] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/schedule/week":
            requests.append(request.url.params.get("date"))
        return await client.send(request)

    monkeypatch.setitem(
        app.dependency_overrides,
        get_schedule_clock,
        lambda: datetime(2026, 10, 20, 12, tzinfo=UTC),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test"
    ) as http:
        api = BackendClient(http, bot_service_key)
        teacher = BotDriver(build_dispatcher(api))
        student = BotDriver(build_dispatcher(api), user_id=5000000001)
        try:
            await _register_teacher(teacher, api)
            invitation = teacher.session.last_message.text.split(
                "Student invitation: "
            )[1]
            payload = parse_qs(urlsplit(invitation).query)["start"][0]
            await student.text("/start " + payload)
            await student.click("Confirm teacher")
            for label in [
                "Use current/default",
                "Use current/default",
                "Use current minutes/off",
                "Use current/default",
                "Register",
            ]:
                await student.click(label)

            teacher_view = await api.view(teacher.user_id, teacher.user_id)
            student_view = await api.view(student.user_id, student.user_id)

            for index, driver in enumerate((teacher, student)):
                message_id = driver.session.last_message.message_id
                await driver.click("This week")
                _assert_no_refresh(driver)
                assert "mon, Oct 19" in (driver.session.last_message.text)
                assert driver.callback_data("◀ Previous week")
                assert driver.callback_data("Next week ▶")

                await driver.click("◀ Previous week")
                assert "mon, Oct 12" in driver.session.last_message.text
                if index == 0:
                    assert "No events" in driver.session.last_message.text
                await driver.click("Next week ▶")
                assert "mon, Oct 19" in driver.session.last_message.text
                await driver.click("◀ Previous week")
                assert "mon, Oct 12" in driver.session.last_message.text
                if index == 0:
                    db_session.add(
                        Event(
                            teacher_id=teacher_view.account.id,
                            user_id=student_view.account.id,
                            event_type="LESSON",
                            start=datetime(2026, 10, 12, 9),
                            end=datetime(2026, 10, 12, 10),
                        )
                    )
                    await db_session.commit()
                await driver.click("Next week ▶")
                await driver.click("◀ Previous week")
                _assert_no_refresh(driver)
                assert "mon, Oct 12" in driver.session.last_message.text
                assert "9:00–10:00" in driver.session.last_message.text
                assert driver.session.last_message.message_id == message_id

            assert requests == [
                None,
                "2026-10-12",
                "2026-10-19",
                "2026-10-12",
                "2026-10-19",
                "2026-10-12",
                None,
                "2026-10-12",
                "2026-10-19",
                "2026-10-12",
                "2026-10-19",
                "2026-10-12",
            ]
        finally:
            await teacher.close()
            await student.close()


async def test_period_switch_and_home_reopen_apply_live_schedule_changes(
    client, db_session, bot_service_key, monkeypatch
) -> None:
    api = BackendClient(client, bot_service_key)
    teacher = BotDriver(build_dispatcher(api))
    student = BotDriver(build_dispatcher(api), user_id=5000000001)
    monkeypatch.setitem(
        app.dependency_overrides,
        get_schedule_clock,
        lambda: datetime(2026, 10, 19, 12, tzinfo=UTC),
    )
    try:
        await _register_teacher(teacher, api)
        invitation = teacher.session.last_message.text.split("Student invitation: ")[1]
        payload = parse_qs(urlsplit(invitation).query)["start"][0]
        await student.text("/start " + payload)
        await student.click("Confirm teacher")
        for label in [
            "Use current/default",
            "Use current/default",
            "Use current minutes/off",
            "Use current/default",
            "Register",
        ]:
            await student.click(label)

        teacher_view = await api.view(teacher.user_id, teacher.user_id)
        student_view = await api.view(student.user_id, student.user_id)
        student_user = await db_session.get(User, student_view.account.id)
        teacher_user = await db_session.get(User, teacher_view.account.id)
        assert student_user is not None and teacher_user is not None
        student_user.full_name = "Original Student"
        one_off = Event(
            teacher_id=teacher_user.id,
            user_id=student_user.id,
            event_type="LESSON",
            start=datetime(2026, 10, 19, 10),
            end=datetime(2026, 10, 19, 11),
        )
        recurrent = RecurrentEvent(
            teacher_id=teacher_user.id,
            user_id=student_user.id,
            event_type="LESSON",
            start=datetime(2026, 10, 19, 11),
            end=datetime(2026, 10, 19, 12),
            interval=7,
            interval_end=datetime(2026, 11, 30, 11),
            recurrence_mode=RecurrenceMode.UTC_STEP.value,
        )
        db_session.add_all([one_off, recurrent])
        await db_session.flush()
        await db_session.commit()

        await teacher.click("Today")
        initial_text = teacher.session.last_message.text
        assert "Original Student" in initial_text
        assert "10:00–11:00" in initial_text
        assert "11:00–12:00" in initial_text

        student_user.full_name = "Updated Student"
        teacher_user.timezone = "Asia/Bangkok"
        db_session.add(
            RecurrentCancels(
                recurrent_event_id=recurrent.id,
                start=datetime(2026, 10, 19, 11),
                end=datetime(2026, 10, 19, 12),
            )
        )
        await db_session.commit()
        await teacher.click("This week")
        await teacher.click("Today")
        _assert_no_refresh(teacher)
        updated_text = teacher.session.last_message.text
        assert "Updated Student" in updated_text
        assert "Timezone: Asia/Bangkok" in updated_text
        assert "17:00–18:00" in updated_text
        assert "11:00–12:00" not in updated_text

        db_session.add(
            Event(
                teacher_id=teacher_user.id,
                user_id=teacher_user.id,
                event_type="VACATION",
                start=datetime(2026, 10, 19, 10, 30),
                end=datetime(2026, 10, 19, 10, 45),
            )
        )
        await db_session.commit()
        await teacher.click("This week")
        await teacher.click("Today")
        _assert_no_refresh(teacher)
        hidden_text = teacher.session.last_message.text
        assert "No events" in hidden_text
        assert "Updated Student" not in hidden_text
    finally:
        await teacher.close()
        await student.close()


async def test_page_navigation_and_period_switch_shrinks_page_controls(
    client, bot_service_key, monkeypatch
) -> None:
    import telegram.schedule_dialogs as schedule_dialogs

    api = BackendClient(client, bot_service_key)
    driver = BotDriver(build_dispatcher(api))
    page_count = [3]

    async def fake_load(_callback, _manager, period, role, requested_date=None):
        return ScheduleDialogState(
            period=period,
            role=role,
            requested_date=(
                requested_date or (date(2026, 10, 19) if period == "week" else None)
            ),
            pages=[
                f"Schedule section {index}" for index in range(1, page_count[0] + 1)
            ],
        )

    monkeypatch.setattr(schedule_dialogs, "_load_schedule", fake_load)
    try:
        await _register_teacher(driver, api)
        message_id = driver.session.last_message.message_id
        await driver.click("This week")
        assert "Schedule section 1" in driver.session.last_message.text
        next_callback = driver.callback_data("Next ▶")
        await asyncio.gather(
            driver.click(data=next_callback), driver.click(data=next_callback)
        )
        assert "Schedule section 3" in driver.session.last_message.text
        await driver.click("◀ Previous")
        assert "Schedule section 2" in driver.session.last_message.text

        stale_next = driver.callback_data("Next ▶")
        page_count[0] = 1
        await driver.click("Today")
        _assert_no_refresh(driver)
        assert "Schedule section 1" in driver.session.last_message.text
        try:
            driver.callback_data("Next ▶")
        except AssertionError:
            pass
        else:
            raise AssertionError("Next page must be hidden after pages shrink")

        await driver.click("Back to home")
        assert driver.session.last_message.message_id == message_id
        await driver.click(data=stale_next)
        assert driver.session.calls[-1].__api_method__ == "answerCallbackQuery"
        assert (
            sum(call.__api_method__ == "sendMessage" for call in driver.session.calls)
            == 1
        )
    finally:
        await driver.close()


async def test_invalid_schedule_output_has_home_and_period_recovery(
    client, bot_service_key, monkeypatch
) -> None:
    import telegram.schedule_dialogs as schedule_dialogs

    api = BackendClient(client, bot_service_key)
    driver = BotDriver(build_dispatcher(api))

    invalid = [True]
    original_formatter = schedule_dialogs.format_schedule_pages

    def maybe_invalid_formatter(*args):
        if invalid[0]:
            raise ValueError("malformed")
        return original_formatter(*args)

    monkeypatch.setattr(
        schedule_dialogs, "format_schedule_pages", maybe_invalid_formatter
    )
    try:
        await _register_teacher(driver, api)
        await driver.click("Today")
        assert "schedule response could not be displayed" in (
            driver.session.last_message.text
        )
        assert "return home and reopen" in driver.session.last_message.text
        _assert_no_refresh(driver)
        assert driver.callback_data("This week")
        assert driver.callback_data("Back to home")
        assert "No events" not in driver.session.last_message.text
        invalid[0] = False
        await driver.click("Back to home")
        await driver.click("Today")
        assert "Today's schedule" in driver.session.last_message.text
        assert "No events" in driver.session.last_message.text
    finally:
        await driver.close()

import json
from itertools import count
from urllib.parse import parse_qs, urlsplit

import httpx

from telegram.api import BackendClient, Registration, SessionView
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver

_TELEGRAM_IDS = count(5_800_000_000, 2)


async def _register_teacher(
    client: httpx.AsyncClient, bot_service_key: str
) -> tuple[BackendClient, SessionView, int]:
    api = BackendClient(client, bot_service_key)
    telegram_id = next(_TELEGRAM_IDS)
    view = await api.register(
        Registration(
            telegram_user_id=telegram_id,
            telegram_private_chat_id=telegram_id,
            full_name="Settings Teacher",
            role="TEACHER",
            timezone="UTC",
        )
    )
    return api, view, telegram_id


async def _register_student(
    api: BackendClient, teacher_code: str
) -> tuple[int, SessionView]:
    telegram_id = next(_TELEGRAM_IDS)
    view = await api.register(
        Registration(
            telegram_user_id=telegram_id,
            telegram_private_chat_id=telegram_id,
            full_name="Settings Student",
            role="STUDENT",
            timezone="UTC",
            teacher_code=teacher_code,
        )
    )
    return telegram_id, view


async def _make_bot(
    client: httpx.AsyncClient,
    bot_service_key: str,
    telegram_id: int,
    *,
    behavior: str | None = None,
) -> tuple[BotDriver, BackendClient, httpx.AsyncClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    fail_profile_refresh = False
    lost_write_response = False

    async def forward(request: httpx.Request) -> httpx.Response:
        nonlocal fail_profile_refresh, lost_write_response
        requests.append(request)
        if (
            behavior == "fail-profile-refresh"
            and fail_profile_refresh
            and request.method == "GET"
            and request.url.path.endswith("/teacher-profile")
        ):
            fail_profile_refresh = False
            raise httpx.ConnectError("profile refresh unavailable", request=request)
        response = await client.send(request)
        if (
            behavior == "fail-profile-refresh"
            and request.method == "PUT"
            and request.url.path.endswith("/teacher-profile")
        ):
            fail_profile_refresh = True
        if (
            behavior == "lose-profile-write"
            and not lost_write_response
            and request.method == "PUT"
            and request.url.path.endswith("/teacher-profile")
        ):
            lost_write_response = True
            raise httpx.ReadTimeout("The profile update response was lost")
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


async def _open_settings(driver: BotDriver) -> None:
    await driver.text("/start")
    await driver.click("Settings")


def _request_body(request: httpx.Request) -> dict:
    return json.loads(request.content)


async def test_teacher_edits_account_and_merged_profile_fields_without_touching_code(
    client, bot_service_key
) -> None:
    _registration_api, registered, telegram_user_id = await _register_teacher(
        client, bot_service_key
    )
    driver, api, http, requests = await _make_bot(
        client, bot_service_key, telegram_user_id
    )
    try:
        await _open_settings(driver)
        assert "settings" in driver.session.last_message.text.lower()
        await driver.click("Account settings")
        await driver.click("Change timezone")
        await driver.text("Asia/Bangkok")
        await driver.click("Change daily digest")
        await driver.text("07:15")
        await driver.click("Save account settings")
        assert "Account settings saved." in driver.session.last_message.text
        updated = await api.view(telegram_user_id, telegram_user_id)
        assert updated.account.timezone == "Asia/Bangkok"
        assert updated.account.notification_lessons_today == 435
        patch = next(
            request
            for request in requests
            if request.method == "PATCH" and request.url.path == "/api/v1/auth/me"
        )
        assert _request_body(patch) == {
            "timezone": "Asia/Bangkok",
            "notification_lessons_today": 435,
        }
        invitation_before = updated.teacher.code

        await driver.click("Teacher settings")
        await driver.click("Work starts")
        await driver.text("09:30")
        await driver.click("Work ends")
        await driver.text("18:30")
        await driver.click("Working days")
        await driver.click("Sat")
        await driver.click("Apply working days")
        await driver.click("Lesson length")
        await driver.text("45")
        await driver.click("Between-lesson break")
        await driver.text("0")
        await driver.click("Consecutive lesson limit")
        await driver.text("7")
        await driver.click("Save teacher settings")
        assert "cannot exceed the daily lesson limit" in (
            driver.session.last_message.text
        )
        assert not any(
            request.method == "PUT" and request.url.path.endswith("/teacher-profile")
            for request in requests
        )

        await driver.click("Lessons per day")
        await driver.text("8")
        await driver.click("Save teacher settings")
        assert "Teacher settings saved." in driver.session.last_message.text
        updated = await api.view(telegram_user_id, telegram_user_id)
        assert updated.teacher.code == invitation_before
        assert updated.account.timezone == "Asia/Bangkok"
        assert (
            updated.teacher.work_start,
            updated.teacher.work_end,
            updated.teacher.working_days,
            updated.teacher.lesson_length,
            updated.teacher.between_lessons_break,
            updated.teacher.max_consecutive_lessons,
            updated.teacher.max_lessons_per_day,
        ) == ("09:30", "18:30", [1, 2, 3, 4, 5, 6], 45, 0, 7, 8)
        teacher_update = next(
            request
            for request in requests
            if request.method == "PUT" and request.url.path.endswith("/teacher-profile")
        )
        assert _request_body(teacher_update) == {
            "work_start": "09:30",
            "work_end": "18:30",
            "working_days": [1, 2, 3, 4, 5, 6],
            "lesson_length": 45,
            "between_lessons_break": 0,
            "max_consecutive_lessons": 7,
            "max_lessons_per_day": 8,
        }
        assert "Automatic breaks are pending" in driver.session.last_message.text

        await driver.click("Back to home")
        assert "Student invitation:" in driver.session.last_message.text
        assert parse_qs(
            urlsplit(
                driver.session.last_message.text.split("Student invitation: ")[1]
            ).query
        )["start"] == [invitation_before]
    finally:
        await _close_bot(driver, http)


async def test_student_reminder_units_and_null_disable_are_saved_as_profile_only(
    client, bot_service_key
) -> None:
    (
        registration_api,
        registered_teacher,
        _teacher_telegram_id,
    ) = await _register_teacher(client, bot_service_key)
    teacher_code = registered_teacher.teacher.code
    telegram_id, registered_student = await _register_student(
        registration_api, teacher_code
    )
    driver, api, http, requests = await _make_bot(client, bot_service_key, telegram_id)
    try:
        await _open_settings(driver)
        await driver.click("Student notifications")
        await driver.click("Change lesson reminder")
        await driver.click("Hours")
        await driver.text("11")
        assert "between 1 and 10" in driver.session.last_message.text
        await driver.text("2")
        await driver.click("Change homework reminder")
        await driver.text("5")
        await driver.click("Save student notifications")
        assert "Student notification settings saved." in (
            driver.session.last_message.text
        )
        updated = await api.view(telegram_id, telegram_id)
        assert updated.student.notification_lesson == 120
        assert updated.student.notification_homework == 5
        assert updated.account.notification_lessons_today == 540
        student_update = next(
            request
            for request in requests
            if request.method == "PUT" and request.url.path.endswith("/student-profile")
        )
        assert _request_body(student_update) == {
            "notification_lesson": 120,
            "notification_homework": 5,
        }

        await driver.click("Student notifications")
        await driver.click("Disable lesson reminder")
        await driver.click("Disable homework reminder")
        await driver.click("Save student notifications")
        updated = await api.view(telegram_id, telegram_id)
        assert updated.student.notification_lesson is None
        assert updated.student.notification_homework is None
        assert updated.account.notification_lessons_today == 540
        disabled_update = [
            request
            for request in requests
            if request.method == "PUT" and request.url.path.endswith("/student-profile")
        ][1]
        assert _request_body(disabled_update) == {
            "notification_lesson": None,
            "notification_homework": None,
        }
        assert registered_student.account.teacher_id == registered_teacher.account.id
    finally:
        await _close_bot(driver, http)


async def test_settings_cancel_discards_draft_and_expired_token_renews_safely(
    client, bot_service_key
) -> None:
    _, _registered, telegram_id = await _register_teacher(client, bot_service_key)
    driver, api, http, requests = await _make_bot(client, bot_service_key, telegram_id)
    try:
        await _open_settings(driver)
        await driver.click("Account settings")
        await driver.click("Change timezone")
        await driver.text("Europe/Paris")
        await driver.click("Cancel changes")
        await driver.click("Back to home")
        await driver.click("Settings")
        await driver.click("Account settings")
        assert "Timezone: UTC" in driver.session.last_message.text

        await driver.click("Change timezone")
        await driver.text("Europe/Berlin")
        api.tokens[telegram_id] = "expired-token"
        await driver.click("Save account settings")
        assert "Account settings saved." in driver.session.last_message.text
        updated = await api.view(telegram_id, telegram_id)
        assert updated.account.timezone == "Europe/Berlin"
        assert updated.account.notification_lessons_today == 540
        patch_requests = [
            request
            for request in requests
            if request.method == "PATCH" and request.url.path == "/api/v1/auth/me"
        ]
        assert len(patch_requests) == 2
        assert _request_body(patch_requests[0]) == {"timezone": "Europe/Berlin"}
        assert _request_body(patch_requests[1]) == {"timezone": "Europe/Berlin"}
    finally:
        await _close_bot(driver, http)


async def test_settings_write_with_lost_response_is_not_replayed(
    client, bot_service_key
) -> None:
    _, registered, telegram_id = await _register_teacher(client, bot_service_key)
    driver, api, http, requests = await _make_bot(
        client,
        bot_service_key,
        telegram_id,
        behavior="lose-profile-write",
    )
    try:
        await _open_settings(driver)
        await driver.click("Teacher settings")
        await driver.click("Lesson length")
        await driver.text("50")
        await driver.click("Save teacher settings")
        assert "outcome is unknown" in driver.session.last_message.text
        assert (
            sum(
                request.method == "PUT"
                and request.url.path.endswith("/teacher-profile")
                for request in requests
            )
            == 1
        )

        await driver.click("Reload settings")
        assert "Latest settings loaded" in driver.session.last_message.text
        updated = await api.view(telegram_id, telegram_id)
        assert updated.teacher.lesson_length == 50
        assert updated.teacher.work_start == registered.teacher.work_start
        assert updated.teacher.work_end == registered.teacher.work_end
        assert updated.teacher.working_days == registered.teacher.working_days
        assert updated.teacher.code == registered.teacher.code
        profile_write = next(
            request
            for request in requests
            if request.method == "PUT" and request.url.path.endswith("/teacher-profile")
        )
        assert _request_body(profile_write) == {"lesson_length": 50}
        assert "Teacher settings saved" not in driver.session.last_message.text
    finally:
        await _close_bot(driver, http)


async def test_profile_save_does_not_claim_refresh_succeeded_after_partial_failure(
    client, bot_service_key
) -> None:
    _, _registered, telegram_id = await _register_teacher(client, bot_service_key)
    driver, api, http, requests = await _make_bot(
        client,
        bot_service_key,
        telegram_id,
        behavior="fail-profile-refresh",
    )
    try:
        await _open_settings(driver)
        await driver.click("Teacher settings")
        await driver.click("Lesson length")
        await driver.text("55")
        await driver.click("Save teacher settings")
        assert "Teacher settings saved." in driver.session.last_message.text
        assert "could not be reloaded" in driver.session.last_message.text
        assert (
            sum(
                request.method == "PUT"
                and request.url.path.endswith("/teacher-profile")
                for request in requests
            )
            == 1
        )

        await driver.click("Reload settings")
        assert "Latest settings loaded" in driver.session.last_message.text
        updated = await api.view(telegram_id, telegram_id)
        assert updated.teacher.lesson_length == 55
    finally:
        await _close_bot(driver, http)

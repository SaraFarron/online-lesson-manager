import asyncio
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import func, select

from backend.auth.models import TeacherProfile, User
from telegram.api import BackendClient, Registration
from telegram.dialogs.home import screen_data
from telegram.dialogs.onboarding import STUDENT_STEPS, TEACHER_STEPS, Step
from telegram.runtime import build_dispatcher
from telegram.shared.inputs import parse_clock
from tests.bot_support import BotDriver


def assert_review_keyboard(driver: BotDriver, titles: tuple[Step, ...]) -> None:
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    edit_rows = markup.inline_keyboard[: len(titles)]
    assert [row[0].text for row in edit_rows] == [step.title for step in titles]
    assert all(len(row) == 1 for row in edit_rows)
    assert [
        button.text for row in markup.inline_keyboard[len(titles) :] for button in row
    ] == ["Register", "Back", "Cancel"]


async def teacher_defaults(driver: BotDriver) -> None:
    await driver.click("Register as teacher")
    for _ in range(3):
        await driver.click("Use current/default")
    await driver.click("Next")
    for _ in range(5):
        await driver.click("Use current/default")
    assert "Review" in driver.session.last_message.text


async def test_teacher_defaults_edit_double_submit_and_invitation(
    client, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    driver = BotDriver(build_dispatcher(api))
    try:
        await driver.text("/start")
        first_id = driver.session.last_message.message_id
        await teacher_defaults(driver)
        assert_review_keyboard(driver, TEACHER_STEPS)
        await driver.click("Lesson length")
        await driver.text("45")
        assert "Lesson length: 45" in driver.session.last_message.text
        submit = driver.callback_data("Register")
        await asyncio.gather(driver.click(data=submit), driver.click(data=submit))
        assert "TEACHER" in driver.session.last_message.text
        assert driver.session.last_message.message_id == first_id
        assert sum(c.__api_method__ == "sendMessage" for c in driver.session.calls) == 1
        view = await api.view(driver.user_id, driver.user_id)
        assert view.teacher.lesson_length == 45
        assert view.teacher.working_days == [1, 2, 3, 4, 5]
        assert view.teacher.between_lessons_break == 5
        assert view.account.notification_lessons_today == 540
        invitation = driver.session.last_message.text.split("Student invitation: ")[1]
        payload = parse_qs(urlsplit(invitation).query)["start"][0]
        assert re.fullmatch(r"t_[A-Z0-9]{6}", view.teacher.code)
        assert payload == view.teacher.code
    finally:
        await driver.close()


async def test_teacher_custom_values_invalid_input_and_cancel(
    client, db_session, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    driver = BotDriver(build_dispatcher(api))
    try:
        await driver.text("/start")
        await driver.click("Register as teacher")
        await driver.text("Invalid/Timezone")
        assert "valid IANA timezone" in driver.session.last_message.text
        await driver.text("Asia/Bangkok")
        await driver.text("09 30")
        await driver.text("08:30")
        assert "later than work start" in driver.session.last_message.text
        await driver.text("18/45")
        await driver.click("Sat")
        await driver.click("Next")
        await driver.text("0")
        assert "between 1" in driver.session.last_message.text
        await driver.text("50")
        await driver.text("0")
        await driver.text("5")
        await driver.text("4")
        assert "at least" in driver.session.last_message.text
        await driver.text("8")
        await driver.click("Disable")
        await driver.click("Register")
        view = await api.view(driver.user_id, driver.user_id)
        assert view.account.timezone == "Asia/Bangkok"
        assert view.account.notification_lessons_today is None
        assert view.teacher.work_start == "09:30" and view.teacher.work_end == "18:45"
        assert view.teacher.working_days == [1, 2, 3, 4, 5, 6]
        assert (
            view.teacher.lesson_length == 50 and view.teacher.between_lessons_break == 0
        )
        assert (
            view.teacher.max_consecutive_lessons == 5
            and view.teacher.max_lessons_per_day == 8
        )
    finally:
        await driver.close()
    other = BotDriver(build_dispatcher(api), user_id=5000000001)
    try:
        await other.text("/start")
        await other.click("Register as teacher")
        await other.click("Use current/default")
        await other.click("Back")
        await other.click("Cancel")
        assert await db_session.scalar(select(func.count()).select_from(User)) == 1
    finally:
        await other.close()


@pytest.mark.parametrize("value", ["09 30", "09:30", "09/30", "9:30"])
def test_flexible_clock(value) -> None:
    assert parse_clock(value) == "09:30"


@pytest.mark.parametrize("value", ["24:00", "09:60", "0900", "x", "09::30"])
def test_invalid_clock(value) -> None:
    with pytest.raises(ValueError):
        parse_clock(value)


async def registered_teacher(api: BackendClient, sender: int = 6000000000):
    return await api.register(
        Registration(
            telegram_user_id=sender,
            telegram_private_chat_id=sender,
            full_name="<Teacher>",
            role="TEACHER",
            timezone="UTC",
        )
    )


async def test_student_deep_link_hours_and_existing_invitation(
    client, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    teacher = await registered_teacher(api)
    driver = BotDriver(build_dispatcher(api))
    try:
        await driver.text("/start " + teacher.teacher.code)
        first_id = driver.session.last_message.message_id
        assert "&lt;Teacher&gt;" in driver.session.last_message.text
        await driver.click("Confirm teacher")
        await driver.text("Asia/Bangkok")
        await driver.text("08/30")
        await driver.click("Hours")
        await driver.text("11")
        assert "between 1 and 10" in driver.session.last_message.text
        await driver.text("2")
        await driver.click("Disable")
        await driver.click("Timezone")
        await driver.text("UTC")
        assert "Review" in driver.session.last_message.text
        assert_review_keyboard(driver, STUDENT_STEPS)
        await driver.click("Register")
        view = await api.view(driver.user_id, driver.user_id)
        assert (
            view.account.role == "STUDENT"
            and view.account.teacher_id == teacher.account.id
        )
        assert (
            view.account.notification_lessons_today == 510
            and view.account.timezone == "UTC"
        )
        assert (
            view.student.notification_lesson == 120
            and view.student.notification_homework is None
        )
        assert driver.session.last_message.message_id == first_id
        assert sum(c.__api_method__ == "sendMessage" for c in driver.session.calls) == 1
        await driver.text("/start")
        assert (
            "Preferences saved. Lesson booking and schedule viewing are available."
            in (driver.session.last_message.text)
        )
        other = await registered_teacher(api, 6000000001)
        await driver.text("/start " + other.teacher.code)
        assert "did not change" in driver.session.last_message.text
        view = await api.view(driver.user_id, driver.user_id)
        assert view.account.teacher_id == teacher.account.id
    finally:
        await driver.close()


async def test_student_manual_code_disabled_and_minute_bounds(
    client, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    teacher = await registered_teacher(api)
    driver = BotDriver(build_dispatcher(api))
    try:
        await driver.text("/start")
        await driver.click("Register as student")
        await driver.text("unknown")
        assert driver.session.last_message.text.startswith("<b>❌ Error</b>\n")
        assert "not found" in driver.session.last_message.text
        await driver.text(teacher.teacher.code)
        await driver.click("Confirm teacher")
        await driver.click("Use current/default")
        await driver.click("Disable")
        await driver.click("Minutes")
        await driver.text("601")
        assert driver.session.last_message.text.startswith("<b>❌ Error</b>\n")
        assert "between 1 and 600" in driver.session.last_message.text
        await driver.text("600")
        await driver.text("3")
        await driver.click("Lesson reminder")
        await driver.click("Disable")
        await driver.click("Daily digest")
        await driver.click("Use current/default")
        await driver.click("Register")
        view = await api.view(driver.user_id, driver.user_id)
        assert view.account.notification_lessons_today is None
        assert (
            view.student.notification_lesson is None
            and view.student.notification_homework == 3
        )
        assert view.account.teacher_id == teacher.account.id
    finally:
        await driver.close()


async def test_returning_teacher_gets_refreshed_short_invitation(
    client, db_session, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    driver = BotDriver(build_dispatcher(api))
    try:
        await driver.text("/start")
        await teacher_defaults(driver)
        await driver.click("Register")
        view = await api.view(driver.user_id, driver.user_id)
        profile = await db_session.scalar(
            select(TeacherProfile).where(TeacherProfile.teacher_id == view.account.id)
        )
        assert profile is not None
        profile.code = "t_NEW123"
        await db_session.commit()

        await driver.text("/start")
        assert "Teacher code: t_NEW123" in driver.session.last_message.text
        assert "Workdays: Mon - Fri" in driver.session.last_message.text
        assert "ISO weekdays" not in driver.session.last_message.text
        assert "Preferences saved." not in driver.session.last_message.text
        invitation = driver.session.last_message.text.split("Student invitation: ")[1]
        assert parse_qs(urlsplit(invitation).query)["start"] == ["t_NEW123"]

        missing_profile_view = view.model_dump(mode="json")
        missing_profile_view["teacher"] = None
        home = await screen_data(
            SimpleNamespace(
                dialog_data={"view": missing_profile_view},
                middleware_data={},
            )
        )
        assert home["feedback"].startswith(
            "<b>⚠️ Attention</b>\nTeacher profile unavailable.\n\n"
        )
        assert "Timezone:" in home["home"]
        assert "Teacher profile unavailable." not in home["home"]
        assert "Preferences saved." not in home["home"]
    finally:
        await driver.close()


async def test_malformed_and_inactive_invitation_recovery(
    client, db_session, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    teacher = await registered_teacher(api)
    user = await db_session.get(User, teacher.account.id)
    user.is_active = False
    await db_session.commit()
    driver = BotDriver(build_dispatcher(api))
    try:
        await driver.text("/start t_bad?payload")
        assert "Invalid invitation" in driver.session.last_message.text
        await driver.text(teacher.teacher.code)
        assert "not found" in driver.session.last_message.text
        await driver.click("Cancel")
        assert await db_session.scalar(select(func.count()).select_from(User)) == 1
    finally:
        await driver.close()

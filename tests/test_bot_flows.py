import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import func, select

from backend.auth.models import StudentProfile, TeacherProfile, User
from telegram.api import BackendClient, CircuitBreaker
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver
from tests.test_bot_onboarding import teacher_defaults


async def test_combined_signup_invitation_restart_and_stale_callback(
    client, db_session, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    teacher = BotDriver(build_dispatcher(api))
    student = BotDriver(build_dispatcher(api), user_id=5000000001)
    try:
        await teacher.text("/start")
        await teacher_defaults(teacher)
        await teacher.click("Register")
        teacher_view = await api.view(teacher.user_id, teacher.user_id)
        invitation = teacher.session.last_message.text.split("Student invitation: ")[1]
        payload = parse_qs(urlsplit(invitation).query)["start"][0]
        await student.text("/start " + payload)
        await student.click("Confirm teacher")
        await student.click("Use current/default")
        await student.click("Use current/default")
        await student.click("Use current minutes/off")
        await student.click("Use current/default")
        stale_submit = student.callback_data("Register")
        await asyncio.gather(
            student.click(data=stale_submit), student.click(data=stale_submit)
        )
        student_view = await api.view(student.user_id, student.user_id)
        assert student_view.account.teacher_id == teacher_view.account.id
        assert student_view.student.notification_lesson == 15
        assert student_view.student.notification_homework == 2
        for driver in [teacher, student]:
            assert (
                sum(c.__api_method__ == "sendMessage" for c in driver.session.calls)
                == 1
            )
            edits = [
                c.message_id
                for c in driver.session.calls
                if c.__api_method__ == "editMessageText"
            ]
            assert edits and len(set(edits)) == 1
        assert await db_session.scalar(select(func.count()).select_from(User)) == 2
        assert (
            await db_session.scalar(select(func.count()).select_from(TeacherProfile))
            == 1
        )
        assert (
            await db_session.scalar(select(func.count()).select_from(StudentProfile))
            == 1
        )
    finally:
        await teacher.close()
        await student.close()
    restarted = BotDriver(
        build_dispatcher(BackendClient(client, bot_service_key)), user_id=5000000001
    )
    try:
        await restarted.text("/start")
        assert "STUDENT" in restarted.session.last_message.text
        await restarted.click(data=stale_submit)
        assert restarted.session.calls[-1].__api_method__ == "answerCallbackQuery"
        user = await db_session.get(User, student_view.account.id)
        user.is_active = False
        await db_session.commit()
        await restarted.text("/start")
        assert "inactive" in restarted.session.last_message.text
        assert "STUDENT:" not in restarted.session.last_message.text
    finally:
        await restarted.close()


async def test_invalid_weekdays_late_workday_and_failed_submit_preserve_draft(
    client, db_session, bot_service_key
) -> None:
    offline = [False]
    now = [0.0]

    async def transport(request):
        if offline[0]:
            raise httpx.ConnectError("test outage", request=request)
        return await client.send(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test", timeout=2
    ) as http:
        api = BackendClient(http, bot_service_key, CircuitBreaker(1, 5, lambda: now[0]))
        driver = BotDriver(build_dispatcher(api))
        try:
            await driver.text("/start")
            await driver.click("Register as teacher")
            await driver.click("Use current/default")
            await driver.text("19:00")
            await driver.text("22 00")
            for day in ["✓ Mon", "✓ Tue", "✓ Wed", "✓ Thu", "✓ Fri"]:
                await driver.click(day)
            await driver.click("Next")
            assert "Select at least one" in driver.session.last_message.text
            await driver.click("Sat")
            await driver.click("Next")
            for _ in range(5):
                await driver.click("Use current/default")
            offline[0] = True
            await driver.click("Register")
            assert "Backend unavailable" in driver.session.last_message.text
            assert "Work starts: 19:00" in driver.session.last_message.text
            assert await db_session.scalar(select(func.count()).select_from(User)) == 0
            offline[0] = False
            now[0] = 6
            await driver.click("Register")
            assert "TEACHER:" in driver.session.last_message.text
            view = await api.view(driver.user_id, driver.user_id)
            assert (
                view.teacher.work_start == "19:00" and view.teacher.work_end == "22:00"
            )
        finally:
            await driver.close()


async def test_invitation_revalidated_at_submit(
    client, db_session, bot_service_key
) -> None:
    api = BackendClient(client, bot_service_key)
    teacher = BotDriver(build_dispatcher(api), user_id=5000000002)
    student = BotDriver(build_dispatcher(api))
    try:
        await teacher.text("/start")
        await teacher_defaults(teacher)
        await teacher.click("Register")
        view = await api.view(teacher.user_id, teacher.user_id)
        await student.text("/start teacher_" + view.teacher.code)
        await student.click("Confirm teacher")
        await student.click("Use current/default")
        await student.click("Use current/default")
        await student.click("Disable")
        await student.click("Disable")
        owner = await db_session.get(User, view.account.id)
        owner.is_active = False
        await db_session.commit()
        await student.click("Register")
        assert "not found" in student.session.last_message.text
        assert "Review" in student.session.last_message.text
        assert await db_session.scalar(select(func.count()).select_from(User)) == 1
    finally:
        await teacher.close()
        await student.close()


@pytest.mark.parametrize("role", ["TEACHER", "STUDENT"])
async def test_incomplete_draft_restart_does_not_create_account(
    client, db_session, bot_service_key, role
) -> None:
    api = BackendClient(client, bot_service_key)
    driver = BotDriver(build_dispatcher(api))
    try:
        await driver.text("/start")
        await driver.click("Register as " + role.lower())
    finally:
        await driver.close()
    restarted = BotDriver(build_dispatcher(BackendClient(client, bot_service_key)))
    try:
        await restarted.text("/start")
        assert restarted.callback_data("Register as teacher")
        assert restarted.callback_data("Register as student")
        assert await db_session.scalar(select(func.count()).select_from(User)) == 0
    finally:
        await restarted.close()

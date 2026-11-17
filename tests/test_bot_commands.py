import httpx
from aiogram.types import BotCommandScopeAllPrivateChats
from sqlalchemy import select

from backend.auth.models import User
from telegram.api import BackendClient
from telegram.runtime import build_dispatcher, publish_commands
from tests.bot_support import BotDriver
from tests.test_bot_settings_dialogs import _register_student, _register_teacher


def _labels(driver: BotDriver) -> list[str]:
    markup = driver.session.last_message.reply_markup
    assert markup is not None
    return [button.text for row in markup.inline_keyboard for button in row]


async def test_commands_are_published_for_private_chats() -> None:
    driver = BotDriver(build_dispatcher())
    try:
        await publish_commands(driver.bot)
        call = driver.session.calls[-1]
        assert call.__api_method__ == "setMyCommands"
        assert call.scope == BotCommandScopeAllPrivateChats()
        assert [command.command for command in call.commands] == [
            "start",
            "settings",
            "vacations",
        ]
    finally:
        await driver.close()


async def test_home_has_no_settings_or_vacations_and_commands_open_dialogs(
    client, bot_service_key
) -> None:
    teacher_api, _teacher, teacher_id = await _register_teacher(client, bot_service_key)
    code = (await teacher_api.view(teacher_id, teacher_id)).teacher.code
    student_id, _student = await _register_student(teacher_api, code)
    drivers = [
        BotDriver(build_dispatcher(BackendClient(client, bot_service_key)), user_id=uid)
        for uid in (teacher_id, student_id)
    ]
    try:
        for driver, role in zip(drivers, ("TEACHER", "STUDENT"), strict=True):
            await driver.text("/start")
            assert driver.session.last_message.text.startswith(role)
            assert "Settings" not in _labels(driver)
            assert "Vacations" not in _labels(driver)

            # A fresh dispatcher state is not required: commands do not depend on /start.
            await driver.text("/settings")
            assert f"{role} settings" in driver.session.last_message.text
            await driver.click("Back to home")
            assert driver.session.last_message.text.startswith(role)
            assert "Today" in _labels(driver)

            await driver.text("/vacations")
            assert "Add vacation" in _labels(driver)
            await driver.click("Back to home")
            assert driver.session.last_message.text.startswith(role)
    finally:
        for driver in drivers:
            await driver.close()


async def test_commands_work_without_prior_start_state(client, bot_service_key) -> None:
    _api, _view, teacher_id = await _register_teacher(client, bot_service_key)
    driver = BotDriver(
        build_dispatcher(BackendClient(client, bot_service_key)), user_id=teacher_id
    )
    try:
        await driver.text("/vacations")
        assert "Add vacation" in _labels(driver)
        await driver.click("Back to home")
        assert driver.session.last_message.text.startswith("TEACHER")
        assert "Teacher code:" in driver.session.last_message.text
    finally:
        await driver.close()


async def test_commands_reject_unregistered_admin_and_group_chats(
    client, bot_service_key, db_session
) -> None:
    _api, _view, admin_id = await _register_teacher(client, bot_service_key)
    admin = (
        await db_session.scalars(select(User).where(User.telegram_user_id == admin_id))
    ).one()
    admin.role = "ADMIN"
    await db_session.commit()
    unknown = BotDriver(
        build_dispatcher(BackendClient(client, bot_service_key)), user_id=5_900_000_001
    )
    admin_driver = BotDriver(
        build_dispatcher(BackendClient(client, bot_service_key)), user_id=admin_id
    )
    try:
        for command, feature in (
            ("/settings", "Settings"),
            ("/vacations", "Vacations"),
        ):
            await unknown.text(command)
            assert "Send /start to register" in unknown.session.last_message.text
            assert unknown.session.last_message.reply_markup is None
            await admin_driver.text(command)
            assert (
                f"{feature} are available to teachers and students"
                in admin_driver.session.last_message.text
            )
        calls = len(unknown.session.calls)
        await unknown.text("/settings", chat_type="group")
        assert len(unknown.session.calls) == calls
    finally:
        await unknown.close()
        await admin_driver.close()


async def test_commands_report_unavailable_backend(bot_service_key) -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(offline), base_url="http://test"
    ) as http:
        driver = BotDriver(build_dispatcher(BackendClient(http, bot_service_key)))
        try:
            await driver.text("/settings")
            assert "Backend unavailable" in driver.session.last_message.text
            assert "Send /start to retry" in driver.session.last_message.text
        finally:
            await driver.close()

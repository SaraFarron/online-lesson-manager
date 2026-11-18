from unittest.mock import AsyncMock

import httpx
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import BotCommandScopeAllPrivateChats, ReplyKeyboardRemove
from sqlalchemy import select

from backend.auth.models import User
from telegram.api import BackendClient
from telegram.runtime import build_dispatcher, publish_commands
from telegram.shared.command_cleanup import remove_stale_reply_keyboard
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


async def test_private_command_messages_are_deleted(client, bot_service_key) -> None:
    _api, _view, teacher_id = await _register_teacher(client, bot_service_key)
    driver = BotDriver(
        build_dispatcher(BackendClient(client, bot_service_key)), user_id=teacher_id
    )
    try:
        for command in ("/start", "/settings", "/vacations", "/unknown arg"):
            await driver.text(command)
            assert driver.update_id in driver.all_deleted_message_ids()
        assert driver.all_deleted_message_ids() == driver.command_message_ids
        assert len(driver.command_message_ids) == 4
    finally:
        await driver.close()


async def test_unknown_command_is_deleted_without_a_reply(
    client, bot_service_key
) -> None:
    driver = BotDriver(
        build_dispatcher(BackendClient(client, bot_service_key)), user_id=5_900_000_002
    )
    try:
        await driver.text("/whatever")
        assert driver.all_deleted_message_ids() == [driver.update_id]
        assert driver.session.last_message is None
        assert not any(
            call.__api_method__ in {"sendMessage", "editMessageText"}
            for call in driver.session.calls
        )
    finally:
        await driver.close()


async def test_group_commands_are_not_deleted(client, bot_service_key) -> None:
    driver = BotDriver(
        build_dispatcher(BackendClient(client, bot_service_key)), user_id=5_900_000_003
    )
    try:
        await driver.text("/start", chat_type="group")
        await driver.text("/unknown", chat_type="group")
        assert driver.all_deleted_message_ids() == []
        assert driver.session.keyboard_removal_calls == []
    finally:
        await driver.close()


async def test_start_removes_stale_reply_keyboard_and_keeps_one_visible_message(
    client, bot_service_key
) -> None:
    _api, _view, teacher_id = await _register_teacher(client, bot_service_key)
    driver = BotDriver(
        build_dispatcher(BackendClient(client, bot_service_key)), user_id=teacher_id
    )
    try:
        await driver.text("/start")
        calls = driver.session.keyboard_removal_calls
        assert [call.__api_method__ for call in calls] == [
            "sendMessage",
            "deleteMessage",
        ]
        assert calls[0].reply_markup == ReplyKeyboardRemove()
        removal_id = driver.session.keyboard_removal_ids[0]
        assert calls[1].message_id == removal_id
        assert driver.session.last_message.text.startswith("TEACHER")
        assert (
            sum(call.__api_method__ == "sendMessage" for call in driver.session.calls)
            == 1
        )

        await driver.text("/settings")
        assert len(driver.session.keyboard_removal_calls) == 2
        await driver.text("/vacations")
        assert len(driver.session.keyboard_removal_calls) == 2
    finally:
        await driver.close()


async def test_start_with_payload_still_registers_after_command_deletion(
    client, bot_service_key
) -> None:
    teacher_api, _teacher, teacher_id = await _register_teacher(client, bot_service_key)
    code = (await teacher_api.view(teacher_id, teacher_id)).teacher.code
    driver = BotDriver(
        build_dispatcher(BackendClient(client, bot_service_key)), user_id=5_900_000_004
    )
    try:
        await driver.text(f"/start t_{code.removeprefix('t_')}")
        assert driver.update_id in driver.all_deleted_message_ids()
        assert "Confirm teacher" in _labels(driver)
    finally:
        await driver.close()


async def test_command_deletion_refusal_is_silent_and_does_not_block_start(
    client, bot_service_key
) -> None:
    _api, _view, teacher_id = await _register_teacher(client, bot_service_key)
    driver = BotDriver(
        build_dispatcher(BackendClient(client, bot_service_key)), user_id=teacher_id
    )
    try:
        driver.session.delete_failure = "message can't be deleted for everyone"
        await driver.text("/start")
        text = driver.session.last_message.text
        assert text.startswith("TEACHER")
        assert "could not remove" not in text
        assert "Delete it manually" not in text
    finally:
        await driver.close()


async def test_keyboard_removal_failures_never_raise() -> None:
    method = SendMessage(chat_id=1, text="x")
    notice = AsyncMock()
    notice.delete.side_effect = TelegramBadRequest(
        method=method, message="message can't be deleted for everyone"
    )
    message = AsyncMock()
    message.answer.return_value = notice
    await remove_stale_reply_keyboard(message)
    message.answer.side_effect = TelegramBadRequest(method=method, message="blocked")
    await remove_stale_reply_keyboard(message)

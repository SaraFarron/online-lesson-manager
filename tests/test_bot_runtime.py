import asyncio

import pytest
from aiogram.exceptions import TelegramBadRequest

from telegram.dialogs import Screens
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver


async def test_editable_screens_and_input() -> None:
    assert Screens.welcome.state == "Screens:welcome"
    assert Screens.details.state == "Screens:details"
    driver = BotDriver(build_dispatcher())
    try:
        await driver.text("/start")
        assert driver.callback_data("Continue").endswith("\x1dcontinue")
        first = driver.session.last_message.message_id
        await driver.click("Continue")
        assert driver.callback_data("Timezone").endswith("\x1dtimezone")
        await driver.click("Timezone")
        await driver.text("Invalid/Zone")
        assert "valid IANA timezone" in driver.session.last_message.text
        await driver.text("Asia/Bangkok")
        assert "Asia/Bangkok" in driver.session.last_message.text
        assert driver.session.last_message.message_id == first
        assert sum(c.__api_method__ == "sendMessage" for c in driver.session.calls) == 1
        assert (
            sum(c.__api_method__ == "answerCallbackQuery" for c in driver.session.calls)
            == 2
        )
    finally:
        await driver.close()


async def test_replacement_is_classified_and_stale_callbacks_acknowledged() -> None:
    driver = BotDriver(build_dispatcher())
    try:
        await driver.text("/start")
        old = driver.session.last_message.message_id
        driver.session.edit_failure = "Bad Request: message to edit not found"
        await driver.click("Continue")
        assert driver.session.last_message.message_id != old
        assert sum(c.__api_method__ == "sendMessage" for c in driver.session.calls) == 2
        driver.session.edit_failure = "Bad Request: can't parse entities"
        with pytest.raises(TelegramBadRequest):
            await driver.click("Timezone")
        assert sum(c.__api_method__ == "sendMessage" for c in driver.session.calls) == 2
        await driver.click(data="expired-intent:continue")
        assert driver.session.calls[-1].__api_method__ == "answerCallbackQuery"
    finally:
        await driver.close()


async def test_private_chats_and_double_clicks() -> None:
    driver = BotDriver(build_dispatcher())
    try:
        await driver.text("/start", chat_type="group")
        assert not driver.session.calls
        await driver.text("/start")
        data = driver.callback_data("Continue")
        await asyncio.gather(driver.click(data=data), driver.click(data=data))
        assert sum(c.__api_method__ == "sendMessage" for c in driver.session.calls) == 1
        assert (
            sum(c.__api_method__ == "answerCallbackQuery" for c in driver.session.calls)
            == 2
        )
    finally:
        await driver.close()

import httpx

from telegram.api import (
    AUTHORIZATION_TTL_SECONDS,
    BackendClient,
    CircuitBreaker,
    ScheduleSnapshotCache,
)
from telegram.runtime import build_dispatcher
from tests.bot_support import BotDriver
from tests.test_bot_onboarding import teacher_defaults


def _button_labels(driver: BotDriver) -> list[str]:
    assert driver.session.last_message is not None
    markup = driver.session.last_message.reply_markup
    if markup is None:
        return []
    return [button.text for row in markup.inline_keyboard for button in row]


async def test_offline_start_is_schedule_only_and_requires_fresh_in_memory_identity(
    client, bot_service_key
) -> None:
    online = True
    requests: list[str] = []
    clock = [0.0]

    async def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if not online:
            raise httpx.ConnectError("test outage", request=request)
        return await client.send(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://test", timeout=2
    ) as http:
        api = BackendClient(
            http,
            bot_service_key,
            breaker=CircuitBreaker(1, 30, lambda: clock[0]),
            schedule_cache=ScheduleSnapshotCache(clock=lambda: clock[0]),
        )
        driver = BotDriver(build_dispatcher(api))
        unknown = BotDriver(build_dispatcher(api), user_id=5_000_000_001)
        restarted: BotDriver | None = None
        try:
            await driver.text("/start")
            await teacher_defaults(driver)
            await driver.click("Register")
            stale_actions = [
                driver.callback_data("Add lesson"),
                driver.callback_data("Settings"),
                driver.callback_data("Homework"),
                driver.callback_data("Manage homework"),
            ]
            await driver.click("Today")
            assert "Today's schedule" in driver.session.last_message.text
            await driver.click("Back to home")
            assert api.offline_schedule_identity(driver.user_id) is not None

            online = False
            requests.clear()
            await driver.text("/start")
            assert _button_labels(driver) == ["Today", "This week"]
            assert (
                "Only previously cached schedules" in driver.session.last_message.text
            )
            assert "TEACHER:" not in driver.session.last_message.text
            requests_after_start = len(requests)

            for callback_data in stale_actions:
                await driver.click(data=callback_data)
                assert len(requests) == requests_after_start
                assert driver.session.calls[-1].__api_method__ == "answerCallbackQuery"
            assert _button_labels(driver) == ["Today", "This week"]

            await driver.click("Today")
            assert "Today's schedule" in driver.session.last_message.text
            assert "Schedule is unavailable" not in driver.session.last_message.text
            await driver.click("Back to home")
            await driver.click("This week")
            assert (
                "Schedule is unavailable right now." in driver.session.last_message.text
            )

            await unknown.text("/start")
            assert "No offline account is created." in unknown.session.last_message.text
            assert _button_labels(unknown) == []

            restarted_api = BackendClient(http, bot_service_key)
            restarted = BotDriver(
                build_dispatcher(restarted_api), user_id=driver.user_id
            )
            await restarted.text("/start")
            assert (
                "No offline account is created." in restarted.session.last_message.text
            )
            assert _button_labels(restarted) == []

            clock[0] += AUTHORIZATION_TTL_SECONDS
            await driver.text("/start")
            assert "No offline account is created." in driver.session.last_message.text
            assert _button_labels(driver) == []
            assert api.offline_schedule_identity(driver.user_id) is None
        finally:
            await driver.close()
            await unknown.close()
            if restarted is not None:
                await restarted.close()

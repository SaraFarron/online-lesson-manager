from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, Message, Update, User


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.last_message: Message | None = None
        self.next_id = 100
        self.edit_failure: str | None = None

    async def close(self) -> None:
        pass

    async def make_request(
        self, bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = None
    ) -> TelegramType:
        self.calls.append(method)
        name = method.__api_method__
        if name == "getMe":
            result: Any = User(
                id=bot.id, is_bot=True, first_name="Test", username="lesson_test_bot"
            )
        elif name in {"sendMessage", "editMessageText"}:
            if name == "editMessageText" and self.edit_failure:
                failure, self.edit_failure = self.edit_failure, None
                raise TelegramBadRequest(method=method, message=failure)
            data = method.model_dump()
            if name == "sendMessage":
                self.next_id += 1
            result = Message(
                message_id=data.get("message_id") or self.next_id,
                date=datetime.now(UTC),
                chat=Chat(id=data["chat_id"], type="private"),
                from_user=User(id=bot.id, is_bot=True, first_name="Test"),
                text=data["text"],
                reply_markup=data.get("reply_markup"),
            ).as_(bot)
            self.last_message = result
        else:
            result = True
        return result

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes]:
        yield b""


class BotDriver:
    def __init__(self, dispatcher: Dispatcher, user_id: int = 5000000000) -> None:
        self.session = RecordingSession()
        self.bot = Bot("123456789:test-token-for-local-recording", session=self.session)
        self.dispatcher = dispatcher
        self.user_id = user_id
        self.update_id = 0

    async def text(self, value: str, chat_type: str = "private") -> None:
        self.update_id += 1
        update = Update.model_validate(
            {
                "update_id": self.update_id,
                "message": {
                    "message_id": self.update_id,
                    "date": 1700000000,
                    "chat": {"id": self.user_id, "type": chat_type},
                    "from": {
                        "id": self.user_id,
                        "is_bot": False,
                        "first_name": "<User>",
                    },
                    "text": value,
                },
            }
        )
        await self.dispatcher.feed_update(self.bot, update)

    def callback_data(self, label: str) -> str:
        assert self.session.last_message is not None
        markup = self.session.last_message.reply_markup
        assert markup is not None
        for row in markup.inline_keyboard:
            for button in row:
                if button.text == label:
                    assert button.callback_data is not None
                    return button.callback_data
        raise AssertionError(f"Button {label!r} not found in {markup}")

    async def click(self, label: str | None = None, data: str | None = None) -> None:
        callback_data = data if data is not None else self.callback_data(label or "")
        self.update_id += 1
        assert self.session.last_message is not None
        update = Update.model_validate(
            {
                "update_id": self.update_id,
                "callback_query": {
                    "id": str(self.update_id),
                    "chat_instance": "local-test",
                    "from": {
                        "id": self.user_id,
                        "is_bot": False,
                        "first_name": "<User>",
                    },
                    "message": self.session.last_message.model_dump(by_alias=True),
                    "data": callback_data,
                },
            }
        )
        await self.dispatcher.feed_update(self.bot, update)

    async def close(self) -> None:
        await self.bot.session.close()
        await self.dispatcher.storage.close()
        await self.dispatcher.fsm.events_isolation.close()

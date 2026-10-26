from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, File, Message, Update, User


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self.last_message: Message | None = None
        self.next_id = 100
        self.edit_failure: str | None = None
        self.files: dict[str, tuple[str, bytes, int]] = {}

    def register_file(
        self,
        file_id: str,
        file_name: str,
        content: bytes,
        advertised_size: int | None = None,
    ) -> None:
        self.files[file_id] = (
            file_name,
            content,
            len(content) if advertised_size is None else advertised_size,
        )

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
        elif name == "getFile":
            file_id = method.model_dump()["file_id"]
            file_name, _content, advertised_size = self.files[file_id]
            result = File(
                file_id=file_id,
                file_unique_id=f"unique-{file_id}",
                file_size=advertised_size,
                file_path=f"documents/{file_id}/{file_name}",
            ).as_(bot)
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
        file_id = url.split("/documents/", maxsplit=1)[-1].split("/", maxsplit=1)[0]
        _file_name, content, _advertised_size = self.files[file_id]
        if not content:
            yield b""
            return
        for start in range(0, len(content), chunk_size):
            yield content[start : start + chunk_size]


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

    async def document(
        self,
        file_name: str,
        content: bytes,
        *,
        advertised_size: int | None = None,
        media_group_id: str | None = None,
    ) -> None:
        self.update_id += 1
        file_id = f"document-{self.update_id}"
        size = len(content) if advertised_size is None else advertised_size
        self.session.register_file(file_id, file_name, content, size)
        document: dict[str, Any] = {
            "file_id": file_id,
            "file_unique_id": f"unique-{file_id}",
            "file_name": file_name,
            "file_size": size,
        }
        message: dict[str, Any] = {
            "message_id": self.update_id,
            "date": 1700000000,
            "chat": {"id": self.user_id, "type": "private"},
            "from": {
                "id": self.user_id,
                "is_bot": False,
                "first_name": "<User>",
            },
            "document": document,
        }
        if media_group_id is not None:
            message["media_group_id"] = media_group_id
        update = Update.model_validate(
            {"update_id": self.update_id, "message": message}
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

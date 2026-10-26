from collections.abc import AsyncGenerator
from uuid import UUID

from aiogram import Bot
from aiogram.types import InputFile

from telegram.api import BackendClient


class HomeworkDownloadFile(InputFile):
    def __init__(
        self,
        client: BackendClient,
        sender_id: int,
        chat_id: int,
        attachment_id: UUID,
        file_name: str,
    ) -> None:
        super().__init__(filename=file_name, chunk_size=65536)
        self.client = client
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.attachment_id = attachment_id

    async def read(self, _bot: Bot) -> AsyncGenerator[bytes]:
        async for chunk in self.client.download_homework_attachment(
            self.sender_id, self.chat_id, self.attachment_id
        ):
            yield chunk

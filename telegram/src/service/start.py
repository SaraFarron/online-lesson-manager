from aiogram.types import Message

from src.service import BackendClient


class StartService:
    def __init__(self, message: Message) -> None:
        self.telegram_id = message.from_user.id
        self.full_name = message.from_user.full_name
        self.username = message.from_user.username
        self.backend = BackendClient()
    
    async def get_user(self):
        return await self.backend.get_user(self.telegram_id)
    
    async def register(self, code: str | None = None):
        teacher_id = await self.backend.get_teacher(code) if code else None
        if teacher_id is None:
            return None
        return await self.backend.create_user(
            telegram_id=self.telegram_id,
            full_name=self.full_name,
            username=self.username,
            teacher_id=teacher_id,
            role="student",  # Currently only student registration is supported
        )
        



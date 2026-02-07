from aiogram.types import Message

from src.schemas import UserCreate
from src.service import BackendClient
from src.service.cache import UserSettings


class UserService:
    def __init__(self, message: Message) -> None:
        if not message.from_user:
            raise ValueError("Message must have a from_user attribute")
        self.telegram_id = message.from_user.id
        self.full_name = message.from_user.full_name
        self.username = message.from_user.username
        self.backend = BackendClient()
    
    async def get_user(self) -> UserSettings | None:
        return await self.backend.get_user(self.telegram_id)
    
    async def register(self, code: str):
        teachers = await self.backend.get_teachers()
        teacher_id = teachers.get(code) if teachers else None
        if teacher_id is None:
            return None
        user = UserCreate(
            telegram_id=self.telegram_id,
            full_name=self.full_name,
            username=self.username,
            code=code,
            role="student",  # Currently only student registration is supported
        )
        return await self.backend.create_user(user)

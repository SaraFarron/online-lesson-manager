from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories import UserSettingsRepository
from app.schemas import UserSettingsUpdate


class UserSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = UserSettingsRepository(session)

    async def get_user_settings(self, user: User):
        return await self.repository.get_by_user_id(user.id)

    async def update_user_settings(self, user: User, user_settings: UserSettingsUpdate):
        user_settings_dict = user_settings.model_dump() | {"user_id": user.id}
        existing_settings = await self.repository.get_by_user_id(user.id)
        if existing_settings:
            return await self.repository.update(existing_settings, user_settings_dict)
        return await self.repository.create(user_settings_dict)

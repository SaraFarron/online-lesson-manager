import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRoles, UserSettings, UserToken
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Repository for User model."""

    def __init__(self, session: AsyncSession):
        super().__init__(User, session)

    async def get_by_token(self, token: str) -> User | None:
        """Get a user by their authentication token."""
        query = select(User).where(User.token == token)
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_by_invite_code(self, invite_code: str) -> User | None:
        """Get a user by their invite code."""
        query = select(User).where(User.invite_code == invite_code)
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_all_active_by_role(self, role: UserRoles) -> list[User]:
        """Get all active users by role."""
        query = select(User).where(User.role == role.value, User.telegram_id.is_not(None))
        result = await self.session.execute(query)
        return list(result.scalars().all())


class UserTokenRepository(BaseRepository[UserToken]):
    """Repository for UserToken model."""

    def __init__(self, session: AsyncSession):
        super().__init__(UserToken, session)

    async def get_by_token(self, token: str) -> UserToken | None:
        """Get a UserToken by its token string."""
        query = select(UserToken).where(UserToken.token == token)
        result = await self.session.execute(query)
        return result.scalars().first()

    @staticmethod
    def generate_unique_token() -> str:
        """Generate a unique token string."""
        return str(uuid.uuid4())


class UserSettingsRepository(BaseRepository[UserSettings]):
    """Repository for UserSettings model."""

    def __init__(self, session: AsyncSession):
        super().__init__(UserSettings, session)

    async def get_by_user_id(self, user_id: int):
        """Get UserSettings by user id."""
        query = select(UserSettings).where(UserSettings.user_id == user_id)
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_all_active(self):
        query = (
            select(UserSettings)
            .join(User)
            .where(
                UserSettings.morning_notification.is_not(None),
                User.telegram_id.is_not(None),
            )
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserToken
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

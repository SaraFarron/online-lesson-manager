from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserToken
from app.repositories import UserRepository, UserTokenRepository


class AuthService:
    """Service layer for authentication logic."""

    def __init__(self, session: AsyncSession):
        self.repository = UserRepository(session)

    async def authenticate_user(self, invite_code: str) -> dict | None:
        """Authenticate a user by their invite code."""
        user = await self.repository.get_by_invite_code(invite_code)
        if not user:
            return None
        token = await self.generate_token(user.id)
        return self.form_response(token, user)

    def form_response(self, token: UserToken, user: User) -> dict:
        """Form the authorized user response dictionary."""
        expires_in = round((token.expires_at - datetime.now(UTC)).total_seconds())
        return {
            "accessToken": token.token,
            "expiresIn": expires_in,
            "user": {
                "id": user.id,
                "name": user.username or user.full_name or user.telegram_id or f"User {user.id}",
            },
        }

    async def generate_token(self, user_id: int) -> UserToken:
        """Generate a new user token."""
        return await UserTokenRepository(self.repository.session).create({
            "user_id": user_id,
            "token": UserTokenRepository.generate_unique_token(),
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        })

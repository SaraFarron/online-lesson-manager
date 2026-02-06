import random
import string
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserToken
from app.repositories import UserRepository, UserTokenRepository
from app.schemas import UserCreate


class AuthService:
    """Service layer for authentication logic."""

    def __init__(self, session: AsyncSession):
        self.repository = UserRepository(session)

    async def authenticate_user(self, invite_code: str) -> dict[str, Any] | None:
        """Authenticate a user by their invite code."""
        user = await self.repository.get_by_invite_code(invite_code)
        if not user:
            return None
        token = await self.generate_token(user.id)
        return self.form_response(token, user)

    def form_response(self, token: UserToken, user: User) -> dict[str, Any]:
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
        return await UserTokenRepository(self.repository.session).create(
            {
                "user_id": user_id,
                "token": UserTokenRepository.generate_unique_token(),
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
            }
        )

    async def register_user(self, user_data: UserCreate) -> User:
        """Register a new user."""
        if not user_data.telegram_id or not user_data.code:
            raise ValueError("Either telegram_id or code must be provided for registration.")
        invite_code = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(4))
        teacher = await self.repository.get_by_invite_code(user_data.code)
        if not teacher:
            raise ValueError("Invalid invite code.")
        new_user = await self.repository.create(
            {
                "telegram_id": user_data.telegram_id,
                "username": user_data.telegram_username,
                "full_name": user_data.telegram_full_name,
                "teacher_id": teacher.id,
                "invite_code": invite_code,
                "role": user_data.role,
            }
        )
        return new_user

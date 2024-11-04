from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import User
from repositories.abstract import Repository
from structures.role import Role


class UserRepo(Repository[User]):
    """User repository for CRUD and other SQL queries."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize user repository as for all users or only for one user."""
        super().__init__(type_model=User, session=session)

    async def new(  # noqa: PLR0913
        self,
        user_id: int,
        user_name: str | None = None,
        first_name: str | None = None,
        second_name: str | None = None,
        is_premium: bool | None = False,  # noqa: FBT002
        role: Role | None = Role.USER,
    ):
        """
        Insert a new user into the database.

        :param user_id: Telegram user id
        :param user_name: Telegram username
        :param first_name: Telegram profile first name
        :param second_name: Telegram profile second name
        :param language_code: Telegram profile language code
        :param is_premium: Telegram user premium status
        :param role: User's role
        """
        return await self.session.merge(
            User(
                id=user_id,
                user_name=user_name,
                first_name=first_name,
                second_name=second_name,
                is_premium=is_premium,
                role=role,
            ),
        )

    async def get_role(self, user_id: int) -> Role:
        """Get user role by id."""
        return await self.session.scalar(
            select(User.role).where(User.id == user_id).limit(1),
        )  # type: ignore  # noqa: PGH003

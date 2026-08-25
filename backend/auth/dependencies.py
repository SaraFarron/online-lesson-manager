import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import service
from backend.auth.exceptions import (
    InactiveUser,
    InsufficientPermissions,
    InvalidCredentials,
)
from backend.auth.models import User
from backend.auth.utils import decode_access_token
from backend.database import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# Reusable Annotated aliases
SessionDep = Annotated[AsyncSession, Depends(get_db)]
TokenDep = Annotated[str, Depends(oauth2_scheme)]


async def get_current_user(token: TokenDep, session: SessionDep) -> User:
    user_id_str = decode_access_token(token)
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError as exc:
        raise InvalidCredentials() from exc

    user = await service.get_user_by_id(session, user_id)
    if user is None:
        raise InvalidCredentials()
    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not current_user.is_active:
        raise InactiveUser()
    return current_user


async def get_current_superuser(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    if not current_user.is_superuser:
        raise InsufficientPermissions()
    return current_user


# Convenience type aliases for use in route signatures
CurrentUser = Annotated[User, Depends(get_current_active_user)]
SuperUser = Annotated[User, Depends(get_current_superuser)]

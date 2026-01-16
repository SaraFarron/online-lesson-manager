from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import async_session_factory
from app.models import User

# Security scheme for Bearer token authentication
bearer_scheme = HTTPBearer()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency that provides an async database session.

    Handles commit on success and rollback on exception.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Type alias for cleaner dependency injection
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


async def verify_service_key(x_service_key: str = Header(...)) -> str:
    """
    Dependency that validates the X-Service-Key header.

    Raises HTTPException 401 if the key is missing or invalid.
    """
    if x_service_key != settings.service_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing service key",
        )
    return x_service_key


# Type alias for service key dependency
ServiceKey = Annotated[str, Depends(verify_service_key)]


async def get_current_user(
    db: DatabaseSession,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> User:
    """
    Dependency that validates the Authorization header token and returns the user.

    Raises HTTPException 401 if the token is missing or invalid.
    """
    token = credentials.credentials

    # Find user by token
    result = await db.execute(select(User).where(User.token == token))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )

    return user


# Type alias for current user dependency
CurrentUser = Annotated[User, Depends(get_current_user)]

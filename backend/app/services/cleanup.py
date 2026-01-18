from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserToken


async def cleanup_expired_tokens(session: AsyncSession) -> int:
    """
    Delete expired tokens from the database.

    Returns the number of deleted rows.
    """
    result = await session.execute(
        delete(UserToken).where(UserToken.expires_at < datetime.now(UTC))
    )
    await session.commit()
    return result.rowcount

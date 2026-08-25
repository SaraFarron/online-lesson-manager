from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings

_is_sqlite = "sqlite" in str(settings.DATABASE_URL)

_engine_kwargs: dict = {"pool_pre_ping": True}
if not _is_sqlite:
    # PostgreSQL – tune the connection pool
    _engine_kwargs["pool_size"] = 10
    _engine_kwargs["max_overflow"] = 20

engine = create_async_engine(str(settings.DATABASE_URL), **_engine_kwargs)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency that yields an async DB session per request."""
    async with SessionFactory() as session:
        yield session

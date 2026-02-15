from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_db
from app.db.base import Base
from app.main import app

# Test database URLs
TEST_DATABASE_URL_ASYNC = "postgresql+asyncpg://postgres:postgres@localhost:5432/lesson_manager_test"
TEST_DATABASE_URL_SYNC = "postgresql+psycopg2://postgres:postgres@localhost:5432/lesson_manager_test"

# Create tables synchronously at module load time
sync_engine = create_engine(TEST_DATABASE_URL_SYNC)
Base.metadata.drop_all(bind=sync_engine)
Base.metadata.create_all(bind=sync_engine)
sync_engine.dispose()

# Async engine for tests - use NullPool to avoid connection reuse issues
test_engine = create_async_engine(
    TEST_DATABASE_URL_ASYNC,
    echo=False,
    poolclass=NullPool,
)
TestSessionFactory = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional database session for tests."""
    async with TestSessionFactory() as session:
        try:
            yield session
        finally:
            await session.rollback()



@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP client for testing endpoints."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()

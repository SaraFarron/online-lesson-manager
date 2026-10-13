from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Import every domain's models so Base.metadata is fully populated before create_all.
from backend.auth import models as auth_models  # noqa: F401
from backend.database import get_db
from backend.events import models as events_models  # noqa: F401
from backend.main import app
from backend.models import Base
from backend.notifications import models as notifications_models  # noqa: F401


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    # In-memory SQLite shared across connections via StaticPool, isolated per test.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with session_factory() as session:
            yield session
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.fixture
async def client(
    db_session: AsyncSession,  # noqa: ARG001 - ensures DB override is applied first
) -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

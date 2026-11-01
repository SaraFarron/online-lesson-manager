import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.database import SessionFactory
from backend.events.time_utils import normalize_utc
from backend.notifications.config import notification_settings

type NotificationProducer = Callable[[AsyncSession, datetime], Awaitable[None]]


async def run_producers_once(
    producers: Sequence[NotificationProducer],
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    now: datetime | None = None,
) -> None:
    if not producers:
        raise RuntimeError("No notification producers are configured")
    timestamp = normalize_utc(now or datetime.now(UTC))
    async with session_factory() as session:
        for producer in producers:
            await producer(session, timestamp)
        await session.commit()


async def run_producer_runner(
    producers: Sequence[NotificationProducer],
    *,
    stop_event: asyncio.Event | None = None,
    poll_seconds: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> None:
    if not producers:
        raise RuntimeError("No notification producers are configured")
    stop = stop_event or asyncio.Event()
    interval = (
        notification_settings.PRODUCER_POLL_SECONDS
        if poll_seconds is None
        else poll_seconds
    )
    if interval < 1:
        raise ValueError("Producer poll interval must be positive")
    while not stop.is_set():
        await run_producers_once(producers, session_factory=session_factory)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue

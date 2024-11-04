import asyncio

from sqlalchemy.ext.asyncio import create_async_engine

from config import DB_ADDRESS
from log import logger, logs
from models import Base


async def init_db() -> None:
    """Initialize database."""
    logger.info(logs.DB_CONNECTING)
    engine = create_async_engine(DB_ADDRESS)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


asyncio.run(init_db())
engine = create_async_engine(DB_ADDRESS)

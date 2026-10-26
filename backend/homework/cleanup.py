import asyncio
import logging

from fastapi import HTTPException

from backend.database import SessionFactory
from backend.homework.config import homework_settings
from backend.homework.service import cleanup_expired_staged_attachments

logger = logging.getLogger(__name__)


async def main() -> int:
    async with SessionFactory() as session:
        try:
            summary = await cleanup_expired_staged_attachments(
                session, homework_settings
            )
        except HTTPException:
            logger.exception("Homework cleanup failed")
            return 1

    logger.info(
        "Homework cleanup removed %s staged attachments and %s orphan files; "
        "%s failures remain for retry",
        summary.deleted_staged,
        summary.deleted_orphans,
        summary.failures,
    )
    return int(summary.failures > 0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(main()))

import uuid
from collections.abc import Awaitable, Callable
from sqlite3 import SQLITE_BUSY, SQLITE_LOCKED

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.models import TeacherScheduleGuard
from backend.exceptions import Conflict, ValidationError


async def run_serialized_schedule_mutation[ResultT](
    session: AsyncSession,
    teacher_id: uuid.UUID,
    operation: Callable[[], Awaitable[ResultT]],
    *,
    rollback_domain_errors: bool = False,
) -> ResultT:
    dialect_name = session.get_bind().dialect.name
    try:
        await _acquire_teacher_schedule_guard(session, teacher_id, dialect_name)
    except OperationalError as exc:
        await session.rollback()
        if _is_sqlite_contention(exc, dialect_name):
            raise Conflict("Schedule is being changed; retry this request.") from exc
        raise
    except BaseException:
        await session.rollback()
        raise

    try:
        return await operation()
    except Conflict, ValidationError:
        if rollback_domain_errors:
            await session.rollback()
        else:
            await session.commit()
        raise
    except BaseException:
        await session.rollback()
        raise


async def _acquire_teacher_schedule_guard(
    session: AsyncSession, teacher_id: uuid.UUID, dialect_name: str
) -> None:
    values = {"teacher_id": teacher_id}
    if dialect_name == "sqlite":
        await session.execute(
            sqlite_insert(TeacherScheduleGuard)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[TeacherScheduleGuard.teacher_id],
                set_={"teacher_id": teacher_id},
            )
        )
    elif dialect_name == "postgresql":
        await session.execute(
            postgresql_insert(TeacherScheduleGuard)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[TeacherScheduleGuard.teacher_id],
                set_={"teacher_id": teacher_id},
            )
        )
    else:
        raise RuntimeError(
            f"Schedule serialization is unsupported for database dialect {dialect_name}"
        )


def _is_sqlite_contention(exc: OperationalError, dialect_name: str) -> bool:
    if dialect_name != "sqlite":
        return False

    error_code = getattr(exc.orig, "sqlite_errorcode", None)
    if error_code is not None and error_code & 0xFF in (SQLITE_BUSY, SQLITE_LOCKED):
        return True

    message = str(exc.orig).lower()
    return "database is locked" in message or "database table is locked" in message

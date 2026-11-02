from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.notifications.models import NotificationIntent


async def insert_notification_intents(
    session: AsyncSession,
    intents: list[dict[str, object]],
) -> None:
    if not intents:
        return
    columns = dict.fromkeys(key for intent in intents for key in intent)
    normalized_intents = [
        {column: intent.get(column) for column in columns} for intent in intents
    ]
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        await session.execute(
            postgresql_insert(NotificationIntent)
            .values(normalized_intents)
            .on_conflict_do_nothing(index_elements=[NotificationIntent.dedupe_key])
        )
    elif dialect == "sqlite":
        await session.execute(
            sqlite_insert(NotificationIntent)
            .values(normalized_intents)
            .on_conflict_do_nothing(index_elements=[NotificationIntent.dedupe_key])
        )
    else:
        raise RuntimeError(f"Unsupported notification producer database: {dialect}")

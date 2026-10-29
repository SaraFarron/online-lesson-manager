import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels
from backend.events.occurrences import Occurrence, OccurrenceSourceKind
from backend.events.time_utils import to_storage_utc


async def apply_displacements(
    session: AsyncSession, occurrences: list[Occurrence], teacher_id: uuid.UUID
) -> None:
    one_off_ids = [
        occurrence.source.id
        for occurrence in occurrences
        if occurrence.source.kind == OccurrenceSourceKind.EVENT
        and occurrence.source.event_type == EventType.LESSON
    ]
    if one_off_ids:
        await session.execute(
            delete(Event)
            .where(Event.teacher_id == teacher_id, Event.id.in_(one_off_ids))
            .execution_options(synchronize_session="fetch")
        )

    session.add_all(
        RecurrentCancels(
            recurrent_event_id=occurrence.source.id,
            start=to_storage_utc(occurrence.start),
            end=to_storage_utc(occurrence.end),
        )
        for occurrence in occurrences
        if occurrence.source.kind == OccurrenceSourceKind.RECURRENT_EVENT
        and occurrence.source.event_type == EventType.LESSON
    )

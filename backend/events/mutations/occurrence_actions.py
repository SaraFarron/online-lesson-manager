import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.mutations.one_off import create_event_in_transaction
from backend.events.mutations.serialization import run_serialized_schedule_mutation
from backend.events.occurrences import (
    Occurrence,
    OccurrenceSource,
    OccurrenceSourceKind,
    RecurrenceSeries,
    expand_recurrence,
)
from backend.events.revisions import build_event_revision
from backend.events.time_utils import from_storage_utc, to_storage_utc
from backend.events.utils import assert_can_assign_user, assert_owns_event
from backend.events.validators import EventValidator
from backend.exceptions import Conflict, ValidationError
from backend.lessons.schemas import (
    LessonCreate,
    RecurrentOccurrenceCancelRequest,
    RecurrentOccurrenceMoveRequest,
)


async def cancel_recurrent_lesson_occurrence(
    session: AsyncSession,
    recurrent_event_id: uuid.UUID,
    request: RecurrentOccurrenceCancelRequest,
    actor: User,
    teacher_id: uuid.UUID,
) -> tuple[RecurrentCancels, str]:
    async def operation() -> tuple[RecurrentCancels, str]:
        recurrent_event, cancellations, occurrence = await _load_occurrence_action(
            session,
            recurrent_event_id,
            request.occurrence_start,
            request.occurrence_end,
            actor,
            teacher_id,
        )
        _ensure_occurrence_not_cancelled(cancellations, occurrence)
        cancellation = RecurrentCancels(
            recurrent_event_id=recurrent_event.id,
            start=to_storage_utc(occurrence.start),
            end=to_storage_utc(occurrence.end),
        )
        session.add(cancellation)
        await session.flush()
        cancellations.append(cancellation)
        revision = build_event_revision(recurrent_event, cancellations)
        await session.commit()
        return cancellation, revision

    return await run_serialized_schedule_mutation(session, teacher_id, operation)


async def move_recurrent_lesson_occurrence(
    session: AsyncSession,
    recurrent_event_id: uuid.UUID,
    request: RecurrentOccurrenceMoveRequest,
    actor: User,
    teacher_id: uuid.UUID,
    validator: EventValidator,
) -> tuple[RecurrentCancels, Event, str]:
    async def operation() -> tuple[RecurrentCancels, Event, str]:
        recurrent_event, cancellations, occurrence = await _load_occurrence_action(
            session,
            recurrent_event_id,
            request.occurrence_start,
            request.occurrence_end,
            actor,
            teacher_id,
        )
        _ensure_occurrence_not_cancelled(cancellations, occurrence)
        await assert_can_assign_user(session, actor, recurrent_event.user_id)

        cancellation = RecurrentCancels(
            recurrent_event_id=recurrent_event.id,
            start=to_storage_utc(occurrence.start),
            end=to_storage_utc(occurrence.end),
        )
        session.add(cancellation)
        await session.flush()

        replacement = await create_event_in_transaction(
            session,
            LessonCreate(
                start=request.replacement_start,
                end=request.replacement_end,
                user_id=recurrent_event.user_id,
            ),
            validator,
            teacher_id,
            actor_is_teacher=actor.role == Roles.TEACHER,
            commit=False,
            post_break_minutes=recurrent_event.post_break_minutes,
        )
        cancellations.append(cancellation)
        revision = build_event_revision(recurrent_event, cancellations)
        await session.commit()
        await session.refresh(replacement)
        return cancellation, replacement, revision

    return await run_serialized_schedule_mutation(
        session, teacher_id, operation, rollback_domain_errors=True
    )


async def _load_occurrence_action(
    session: AsyncSession,
    recurrent_event_id: uuid.UUID,
    occurrence_start: datetime,
    occurrence_end: datetime,
    actor: User,
    teacher_id: uuid.UUID,
) -> tuple[RecurrentEvent, list[RecurrentCancels], Occurrence]:
    recurrent_event = await session.get(
        RecurrentEvent, recurrent_event_id, populate_existing=True
    )
    if (
        recurrent_event is None
        or recurrent_event.event_type != EventType.LESSON.value
        or recurrent_event.teacher_id != teacher_id
    ):
        raise ValidationError("Recurring lesson not found")
    assert_owns_event(actor, recurrent_event)

    cancellations = list(
        (
            await session.scalars(
                select(RecurrentCancels)
                .where(RecurrentCancels.recurrent_event_id == recurrent_event.id)
                .order_by(RecurrentCancels.start, RecurrentCancels.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    start = from_storage_utc(to_storage_utc(occurrence_start))
    end = from_storage_utc(to_storage_utc(occurrence_end))
    try:
        range_end = start + timedelta(microseconds=1)
    except OverflowError as exc:
        raise ValidationError(
            "Occurrence start is outside the supported range"
        ) from exc
    source = OccurrenceSource(
        kind=OccurrenceSourceKind.RECURRENT_EVENT,
        id=recurrent_event.id,
        teacher_id=recurrent_event.teacher_id,
        user_id=recurrent_event.user_id,
        event_type=EventType.LESSON,
    )
    series = RecurrenceSeries(
        source=source,
        start=from_storage_utc(recurrent_event.start),
        end=from_storage_utc(recurrent_event.end),
        interval=recurrent_event.interval,
        interval_end=(
            from_storage_utc(recurrent_event.interval_end)
            if recurrent_event.interval_end is not None
            else None
        ),
        recurrence_mode=recurrent_event.recurrence_mode,
        recurrence_timezone=recurrent_event.recurrence_timezone,
    )
    occurrence = next(
        (
            item
            for item in expand_recurrence(series, start, range_end)
            if item.start == start and item.end == end
        ),
        None,
    )
    if occurrence is None:
        raise ValidationError("Occurrence identity is not part of this series")
    return recurrent_event, cancellations, occurrence


def _ensure_occurrence_not_cancelled(
    cancellations: list[RecurrentCancels], occurrence: Occurrence
) -> None:
    matching = [
        cancellation
        for cancellation in cancellations
        if from_storage_utc(cancellation.start) == occurrence.start
    ]
    if matching:
        if any(from_storage_utc(item.end) != occurrence.end for item in matching):
            raise ValidationError("Stored cancellation does not match its occurrence")
        raise Conflict(
            {
                "code": "occurrence_already_cancelled",
                "message": "This lesson occurrence has already been cancelled",
            }
        )

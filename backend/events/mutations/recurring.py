import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import User
from backend.auth.service import get_user_by_id
from backend.breaks.schemas import RecurrentBreakCreate
from backend.constants import EventType
from backend.events.booking_policy import prepare_lesson_write
from backend.events.constants import RecurrenceMode
from backend.events.models import RecurrentCancels, RecurrentEvent
from backend.events.mutations.displacement import apply_displacements
from backend.events.mutations.serialization import run_serialized_schedule_mutation
from backend.events.occurrences import (
    OccurrenceSource,
    OccurrenceSourceKind,
    RecurrenceSeries,
    expand_recurrence,
)
from backend.events.revisions import (
    _assert_expected_revision,
    build_event_revision,
)
from backend.events.schemas import RecurrentEventCreate, RecurrentEventUpdate
from backend.events.time_utils import from_storage_utc, to_storage_utc
from backend.events.utils import assert_can_assign_user, assert_owns_event
from backend.events.validators import EventValidator
from backend.exceptions import ValidationError


async def create_recurrent_event(
    session: AsyncSession,
    event_data: RecurrentEventCreate | RecurrentBreakCreate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
) -> RecurrentEvent:
    return await run_serialized_schedule_mutation(
        session,
        teacher_id,
        lambda: _create_recurrent_event(
            session,
            event_data,
            validator,
            teacher_id,
            confirm_displacement=confirm_displacement,
            displacement_token=displacement_token,
            actor_is_teacher=actor_is_teacher,
        ),
    )


async def _create_recurrent_event(
    session: AsyncSession,
    event_data: RecurrentEventCreate | RecurrentBreakCreate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
) -> RecurrentEvent:
    interval_end = event_data.interval_end
    if interval_end is None and (
        event_data.event_type != EventType.LESSON or event_data.interval != 7
    ):
        raise ValidationError("Only weekly lessons may have no recurrence cutoff")

    teacher = await get_user_by_id(session, teacher_id)
    if teacher is None:
        raise ValidationError(f"Teacher with ID {teacher_id} does not exist")

    teacher_profile = None
    teacher_timezone = None
    if event_data.event_type == EventType.LESSON:
        event_data, teacher_profile, teacher_timezone = await prepare_lesson_write(
            session,
            event_data,
            teacher_id,
            actor_is_teacher=actor_is_teacher,
        )
    if event_data.end is None:
        raise ValidationError("Event end time is required")

    displaced = await validator.validate_all(
        session,
        event_data,
        teacher_id=teacher_id,
        user_id=event_data.user_id,
        confirm_displacement=confirm_displacement,
        displacement_token=displacement_token,
        teacher_profile=teacher_profile,
        teacher_timezone=teacher_timezone,
        post_break_minutes=(
            teacher_profile.between_lessons_break
            if event_data.event_type == EventType.LESSON and teacher_profile is not None
            else None
        ),
    )
    await apply_displacements(session, displaced, teacher_id)

    recurrent_event = RecurrentEvent(
        start=to_storage_utc(event_data.start),
        end=to_storage_utc(event_data.end),
        user_id=event_data.user_id,
        teacher_id=teacher_id,
        event_type=event_data.event_type,
        interval=event_data.interval,
        interval_end=(
            to_storage_utc(interval_end) if interval_end is not None else None
        ),
        recurrence_mode=RecurrenceMode.TEACHER_LOCAL.value,
        recurrence_timezone=teacher.timezone,
        post_break_minutes=(
            teacher_profile.between_lessons_break
            if event_data.event_type == EventType.LESSON and teacher_profile is not None
            else 0
        ),
    )
    session.add(recurrent_event)
    await session.commit()
    await session.refresh(recurrent_event)
    return recurrent_event


async def update_recurrent_event(
    session: AsyncSession,
    recurrent_event: RecurrentEvent,
    event_data: RecurrentEventUpdate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    actor: User | None = None,
    expected_revision: str | None = None,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
) -> RecurrentEvent:
    return await run_serialized_schedule_mutation(
        session,
        teacher_id,
        lambda: _update_recurrent_event(
            session,
            recurrent_event,
            event_data,
            validator,
            teacher_id,
            actor=actor,
            expected_revision=expected_revision,
            confirm_displacement=confirm_displacement,
            displacement_token=displacement_token,
            actor_is_teacher=actor_is_teacher,
        ),
    )


async def _update_recurrent_event(
    session: AsyncSession,
    recurrent_event: RecurrentEvent,
    event_data: RecurrentEventUpdate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    actor: User | None = None,
    expected_revision: str | None = None,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
) -> RecurrentEvent:
    interval_end = event_data.interval_end
    fresh_event = await session.get(
        RecurrentEvent, recurrent_event.id, populate_existing=True
    )
    if fresh_event is None or fresh_event.teacher_id != teacher_id:
        raise ValidationError("Recurring event not found")
    recurrent_event = fresh_event
    if interval_end is None and (
        recurrent_event.event_type != EventType.LESSON.value or event_data.interval != 7
    ):
        raise ValidationError("Only weekly lessons may have no recurrence cutoff")
    if actor is not None:
        assert_owns_event(actor, recurrent_event)
        await assert_can_assign_user(session, actor, event_data.user_id)
    existing_cancellations = list(
        (
            await session.scalars(
                select(RecurrentCancels)
                .where(RecurrentCancels.recurrent_event_id == recurrent_event.id)
                .order_by(RecurrentCancels.start, RecurrentCancels.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    if expected_revision is not None:
        _assert_expected_revision(
            expected_revision,
            build_event_revision(recurrent_event, existing_cancellations),
        )

    teacher_profile = None
    teacher_timezone = None
    if validator.expected_event_type == EventType.LESSON:
        event_data, teacher_profile, teacher_timezone = await prepare_lesson_write(
            session,
            event_data,
            teacher_id,
            actor_is_teacher=actor_is_teacher,
        )
    if event_data.end is None:
        raise ValidationError("Event end time is required")

    recurrence_changed = (
        recurrent_event.start != to_storage_utc(event_data.start)
        or recurrent_event.end != to_storage_utc(event_data.end)
        or recurrent_event.interval != event_data.interval
        or recurrent_event.interval_end
        != (to_storage_utc(interval_end) if interval_end is not None else None)
    )
    replacement_cancellations = (
        _validate_cancellations(recurrent_event, event_data)
        if event_data.cancels is not None
        else None
    )
    if replacement_cancellations is not None:
        candidate_cancellations = [
            cancellation_start for cancellation_start, _ in replacement_cancellations
        ]
    elif recurrence_changed:
        candidate_cancellations = []
    else:
        candidate_cancellations = [
            from_storage_utc(cancellation.start)
            for cancellation in existing_cancellations
        ]

    displaced = await validator.validate_all(
        session,
        event_data,
        teacher_id=teacher_id,
        user_id=event_data.user_id,
        exclude_event_id=recurrent_event.id,
        recurrence_timezone=recurrent_event.recurrence_timezone,
        recurrence_mode=recurrent_event.recurrence_mode,
        canceled_starts=candidate_cancellations,
        confirm_displacement=confirm_displacement,
        displacement_token=displacement_token,
        teacher_profile=teacher_profile,
        teacher_timezone=teacher_timezone,
        post_break_minutes=recurrent_event.post_break_minutes,
    )
    await apply_displacements(session, displaced, teacher_id)

    recurrent_event.start = to_storage_utc(event_data.start)
    recurrent_event.end = to_storage_utc(event_data.end)
    recurrent_event.user_id = event_data.user_id
    recurrent_event.interval = event_data.interval
    recurrent_event.interval_end = (
        to_storage_utc(interval_end) if interval_end is not None else None
    )
    recurrent_event.teacher_id = teacher_id

    if recurrence_changed or replacement_cancellations is not None:
        await session.execute(
            delete(RecurrentCancels).where(
                RecurrentCancels.recurrent_event_id == recurrent_event.id
            )
        )
        if replacement_cancellations is not None:
            session.add_all(
                RecurrentCancels(
                    recurrent_event_id=recurrent_event.id,
                    start=cancel_start,
                    end=cancel_end,
                )
                for cancel_start, cancel_end in replacement_cancellations
            )

    session.add(recurrent_event)
    await session.commit()
    await session.refresh(recurrent_event)
    return recurrent_event


def _validate_cancellations(
    recurrent_event: RecurrentEvent,
    event_data: RecurrentEventUpdate,
) -> list[tuple[datetime, datetime]]:
    interval_end = event_data.interval_end
    try:
        event_type = EventType(recurrent_event.event_type)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"Invalid stored event type: {recurrent_event.event_type}"
        ) from exc

    source = OccurrenceSource(
        kind=OccurrenceSourceKind.RECURRENT_EVENT,
        id=recurrent_event.id,
        teacher_id=recurrent_event.teacher_id,
        user_id=event_data.user_id,
        event_type=event_type,
    )
    series = RecurrenceSeries(
        source=source,
        start=event_data.start,
        end=event_data.end,
        interval=event_data.interval,
        interval_end=interval_end,
        recurrence_mode=recurrent_event.recurrence_mode,
        recurrence_timezone=recurrent_event.recurrence_timezone,
    )
    duration = to_storage_utc(event_data.end) - to_storage_utc(event_data.start)
    validated: list[tuple[datetime, datetime]] = []
    seen_starts: set[datetime] = set()

    for cancel in event_data.cancels or []:
        cancel_start = to_storage_utc(cancel.start)
        cancel_end = to_storage_utc(cancel.end)
        if cancel_start in seen_starts:
            raise ValidationError("Duplicate cancellation for the same occurrence")
        seen_starts.add(cancel_start)
        if cancel_end - cancel_start != duration:
            raise ValidationError(
                "Cancellation must match the full generated occurrence interval"
            )
        validated.append((cancel_start, cancel_end))

    if not validated:
        return validated

    try:
        probe_start = from_storage_utc(min(start for start, _ in validated))
        probe_end = from_storage_utc(max(start for start, _ in validated)) + timedelta(
            microseconds=1
        )
    except OverflowError as exc:
        raise ValidationError("Cancellation is outside the supported range") from exc
    generated = {
        occurrence.start: occurrence.end
        for occurrence in expand_recurrence(series, probe_start, probe_end)
    }
    if any(
        generated.get(from_storage_utc(start)) != from_storage_utc(end)
        for start, end in validated
    ):
        raise ValidationError("Cancellation does not match a generated occurrence")

    return validated

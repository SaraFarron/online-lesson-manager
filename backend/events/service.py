import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from sqlite3 import SQLITE_BUSY, SQLITE_LOCKED

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.auth.service import get_user_by_id
from backend.breaks.schemas import RecurrentBreakCreate
from backend.constants import EventType
from backend.events.booking_policy import prepare_lesson_write
from backend.events.constants import RecurrenceMode
from backend.events.management import build_event_revision
from backend.events.models import (
    Event,
    RecurrentCancels,
    RecurrentEvent,
    TeacherScheduleGuard,
)
from backend.events.occurrences import (
    Occurrence,
    OccurrenceSource,
    OccurrenceSourceKind,
    RecurrenceSeries,
    expand_recurrence,
)
from backend.events.schemas import (
    EventCreate,
    EventUpdate,
    RecurrentEventCreate,
    RecurrentEventUpdate,
)
from backend.events.time_utils import from_storage_utc, to_storage_utc
from backend.events.utils import assert_can_assign_user, assert_owns_event
from backend.events.validators import EventValidator
from backend.exceptions import Conflict, ValidationError
from backend.lessons.schemas import (
    LessonCreate,
    RecurrentOccurrenceCancelRequest,
    RecurrentOccurrenceMoveRequest,
)


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


async def create_event(
    session: AsyncSession,
    event_data: EventCreate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
) -> Event:
    return await run_serialized_schedule_mutation(
        session,
        teacher_id,
        lambda: _create_event(
            session,
            event_data,
            validator,
            teacher_id,
            confirm_displacement=confirm_displacement,
            displacement_token=displacement_token,
            actor_is_teacher=actor_is_teacher,
        ),
    )


async def _create_event(
    session: AsyncSession,
    event_data: EventCreate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
    commit: bool = True,
    post_break_minutes: int | None = None,
) -> Event:
    teacher_profile = None
    teacher_timezone = None
    saved_post_break_minutes = 0
    if event_data.event_type == EventType.LESSON:
        event_data, teacher_profile, teacher_timezone = await prepare_lesson_write(
            session,
            event_data,
            teacher_id,
            actor_is_teacher=actor_is_teacher,
        )
        if teacher_profile is not None:
            saved_post_break_minutes = teacher_profile.between_lessons_break
        if post_break_minutes is not None:
            saved_post_break_minutes = post_break_minutes
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
        post_break_minutes=saved_post_break_minutes,
    )
    await _apply_displacements(session, displaced, teacher_id)

    event = Event(
        start=to_storage_utc(event_data.start),
        end=to_storage_utc(event_data.end),
        user_id=event_data.user_id,
        teacher_id=teacher_id,
        event_type=event_data.event_type,
        post_break_minutes=saved_post_break_minutes,
    )
    session.add(event)
    if commit:
        await session.commit()
        await session.refresh(event)
    else:
        await session.flush()
    return event


async def update_event(
    session: AsyncSession,
    event: Event,
    event_data: EventUpdate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    actor: User | None = None,
    expected_revision: str | None = None,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
) -> Event:
    return await run_serialized_schedule_mutation(
        session,
        teacher_id,
        lambda: _update_event(
            session,
            event,
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


async def _update_event(
    session: AsyncSession,
    event: Event,
    event_data: EventUpdate,
    validator: EventValidator,
    teacher_id: uuid.UUID,
    *,
    actor: User | None = None,
    expected_revision: str | None = None,
    confirm_displacement: bool = False,
    displacement_token: str | None = None,
    actor_is_teacher: bool = False,
) -> Event:
    fresh_event = await session.get(Event, event.id, populate_existing=True)
    if fresh_event is None or fresh_event.teacher_id != teacher_id:
        raise ValidationError("Lesson or event not found")
    event = fresh_event
    if actor is not None:
        assert_owns_event(actor, event)
        await assert_can_assign_user(session, actor, event_data.user_id)
    if expected_revision is not None:
        _assert_expected_revision(expected_revision, build_event_revision(event, ()))

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

    displaced = await validator.validate_all(
        session,
        event_data,
        teacher_id=teacher_id,
        user_id=event_data.user_id,
        exclude_event_id=event.id,
        confirm_displacement=confirm_displacement,
        displacement_token=displacement_token,
        teacher_profile=teacher_profile,
        teacher_timezone=teacher_timezone,
        post_break_minutes=event.post_break_minutes,
    )
    await _apply_displacements(session, displaced, teacher_id)

    event.start = to_storage_utc(event_data.start)
    event.end = to_storage_utc(event_data.end)
    event.user_id = event_data.user_id
    event.teacher_id = teacher_id
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def delete_event(
    session: AsyncSession,
    event: Event | RecurrentEvent,
    *,
    actor: User | None = None,
    expected_revision: str | None = None,
) -> None:
    await run_serialized_schedule_mutation(
        session,
        event.teacher_id,
        lambda: _delete_event(session, event, actor, expected_revision),
    )


async def _delete_event(
    session: AsyncSession,
    event: Event | RecurrentEvent,
    actor: User | None,
    expected_revision: str | None,
) -> None:
    if isinstance(event, RecurrentEvent):
        fresh_recurrent = await session.get(
            RecurrentEvent, event.id, populate_existing=True
        )
        if fresh_recurrent is None or fresh_recurrent.teacher_id != event.teacher_id:
            raise ValidationError("Event not found")
        if actor is not None:
            assert_owns_event(actor, fresh_recurrent)
        cancellations = list(
            (
                await session.scalars(
                    select(RecurrentCancels)
                    .where(RecurrentCancels.recurrent_event_id == fresh_recurrent.id)
                    .order_by(RecurrentCancels.start, RecurrentCancels.id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        if expected_revision is not None:
            _assert_expected_revision(
                expected_revision,
                build_event_revision(fresh_recurrent, cancellations),
            )
        await session.execute(
            delete(RecurrentCancels).where(
                RecurrentCancels.recurrent_event_id == fresh_recurrent.id
            )
        )
        await session.delete(fresh_recurrent)
    else:
        fresh_one_off = await session.get(Event, event.id, populate_existing=True)
        if fresh_one_off is None or fresh_one_off.teacher_id != event.teacher_id:
            raise ValidationError("Event not found")
        if actor is not None:
            assert_owns_event(actor, fresh_one_off)
        if expected_revision is not None:
            _assert_expected_revision(
                expected_revision, build_event_revision(fresh_one_off, ())
            )
        await session.delete(fresh_one_off)
    await session.commit()


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
    await _apply_displacements(session, displaced, teacher_id)

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
    await _apply_displacements(session, displaced, teacher_id)

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

        replacement = await _create_event(
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


def _assert_expected_revision(expected: str, current: str) -> None:
    if expected != current:
        raise Conflict(
            {
                "code": "stale_event_revision",
                "message": "This event changed; reload it before editing",
            }
        )


async def _apply_displacements(
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

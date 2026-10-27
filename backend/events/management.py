import hashlib
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.constants import EventType
from backend.events.constants import (
    AUTOMATIC_BREAK_READ_ONLY_DETAIL,
    RecurrenceMode,
)
from backend.events.management_schemas import (
    EventManagementDetail,
    EventManagementItem,
    EventManagementPage,
    ManagedOccurrence,
    ManagedOccurrenceStatus,
    VisibilityStatus,
)
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.occurrences import (
    OccurrenceSource,
    OccurrenceSourceKind,
    RecurrenceSeries,
    expand_recurrence,
)
from backend.events.schemas import RecurrentCancel
from backend.events.time_utils import from_storage_utc, normalize_utc
from backend.events.utils import assert_owns_event
from backend.exceptions import BadRequest, Conflict, Forbidden, ValidationError

_UNNAMED_STUDENT = "Unnamed student"
_PERSONAL_SUBJECT_LABELS = {
    EventType.LESSON: "Personal lesson",
    EventType.BREAK: "Teacher break",
    EventType.VACATION: "Teacher vacation",
}


async def list_managed_events(
    session: AsyncSession,
    actor: User,
    event_type: EventType,
    *,
    is_recurrent: bool,
    skip: int,
    limit: int,
    subject_id: uuid.UUID | None,
) -> EventManagementPage:
    teacher_id, scoped_subject_id = await _resolve_scope(
        session, actor, subject_id, teacher_only=event_type == EventType.BREAK
    )
    rows: list[Event | RecurrentEvent] = []
    if is_recurrent:
        filters = [
            RecurrentEvent.teacher_id == teacher_id,
            RecurrentEvent.event_type == event_type.value,
        ]
        if scoped_subject_id is not None:
            filters.append(RecurrentEvent.user_id == scoped_subject_id)
        total = await session.scalar(
            select(func.count()).select_from(RecurrentEvent).where(*filters)
        )
        rows.extend(
            (
                await session.scalars(
                    select(RecurrentEvent)
                    .where(*filters)
                    .order_by(RecurrentEvent.start, RecurrentEvent.id)
                    .offset(skip)
                    .limit(limit)
                )
            ).all()
        )
    else:
        filters = [
            Event.teacher_id == teacher_id,
            Event.event_type == event_type.value,
        ]
        if scoped_subject_id is not None:
            filters.append(Event.user_id == scoped_subject_id)
        total = await session.scalar(
            select(func.count()).select_from(Event).where(*filters)
        )
        rows.extend(
            (
                await session.scalars(
                    select(Event)
                    .where(*filters)
                    .order_by(Event.start, Event.id)
                    .offset(skip)
                    .limit(limit)
                )
            ).all()
        )
    cancellation_counts = await _cancellation_counts(
        session, [row.id for row in rows] if is_recurrent else []
    )
    items = [
        await _management_item(
            session,
            actor,
            teacher_id,
            row,
            is_recurrent=is_recurrent,
            cancelled_occurrences=cancellation_counts.get(row.id, 0),
        )
        for row in rows
    ]
    return EventManagementPage(items=items, total=total or 0)


async def get_managed_event(
    session: AsyncSession,
    actor: User,
    event_id: uuid.UUID,
    event_type: EventType,
    *,
    is_recurrent: bool,
    occurrence_start: datetime | None = None,
) -> EventManagementDetail:
    teacher_id, _ = await _resolve_scope(
        session, actor, None, teacher_only=event_type == EventType.BREAK
    )
    event: Event | RecurrentEvent | None
    if is_recurrent:
        event = await session.get(RecurrentEvent, event_id)
    else:
        event = await session.get(Event, event_id)
    if (
        event is not None
        and event.teacher_id == teacher_id
        and event_type == EventType.BREAK
        and event.event_type == EventType.LESSON.value
        and event.post_break_minutes > 0
    ):
        raise ValidationError(AUTOMATIC_BREAK_READ_ONLY_DETAIL)
    if event is None or event.event_type != event_type.value:
        raise ValidationError(f"{event_type.value.title()} not found")

    assert_owns_event(actor, event)
    subject_label = await _subject_label(
        session, actor, teacher_id, event.user_id, EventType(event.event_type)
    )
    if isinstance(event, Event):
        if is_recurrent:
            raise ValidationError("Recurring event not found")
        visibility = await _one_off_visibility(session, event)
        return EventManagementDetail(
            id=event.id,
            event_type=EventType(event.event_type),
            start=from_storage_utc(event.start),
            end=from_storage_utc(event.end),
            user_id=event.user_id,
            subject_label=subject_label,
            is_recurrent=False,
            cancelled_occurrences=0,
            visibility_status=visibility,
            post_break_minutes=event.post_break_minutes,
            revision=build_event_revision(event, ()),
        )

    if not is_recurrent:
        raise ValidationError("One-off event not found")
    try:
        recurrence_mode = RecurrenceMode(event.recurrence_mode)
    except ValueError as exc:
        raise ValidationError("Invalid stored recurrence mode") from exc
    cancellations = list(
        (
            await session.scalars(
                select(RecurrentCancels)
                .where(RecurrentCancels.recurrent_event_id == event.id)
                .order_by(RecurrentCancels.start, RecurrentCancels.id)
            )
        ).all()
    )
    cancellation_schemas = [
        RecurrentCancel(
            start=from_storage_utc(cancel.start),
            end=from_storage_utc(cancel.end),
        )
        for cancel in cancellations
    ]
    inspected = None
    if occurrence_start is not None:
        inspected = await _inspect_occurrence(
            session, event, cancellations, occurrence_start
        )
    return EventManagementDetail(
        id=event.id,
        event_type=EventType(event.event_type),
        start=from_storage_utc(event.start),
        end=from_storage_utc(event.end),
        user_id=event.user_id,
        subject_label=subject_label,
        is_recurrent=True,
        cancelled_occurrences=len(cancellations),
        post_break_minutes=event.post_break_minutes,
        interval=event.interval,
        interval_end=(
            from_storage_utc(event.interval_end)
            if event.interval_end is not None
            else None
        ),
        recurrence_mode=recurrence_mode,
        recurrence_timezone=event.recurrence_timezone,
        cancels=cancellation_schemas,
        revision=build_event_revision(event, cancellations),
        inspected_occurrence=inspected,
    )


def build_event_revision(
    event: Event | RecurrentEvent,
    cancellations: tuple[RecurrentCancels, ...] | list[RecurrentCancels],
) -> str:
    payload: dict[str, object] = {
        "id": str(event.id),
        "teacher_id": str(event.teacher_id),
        "user_id": str(event.user_id),
        "event_type": event.event_type,
        "start": _timestamp(event.start),
        "end": _timestamp(event.end),
        "post_break_minutes": event.post_break_minutes,
        "updated_at": normalize_utc(event.updated_at).isoformat(),
        "cancellations": [
            {
                "id": str(cancel.id),
                "start": _timestamp(cancel.start),
                "end": _timestamp(cancel.end),
                "updated_at": normalize_utc(cancel.updated_at).isoformat(),
            }
            for cancel in cancellations
        ],
    }
    if isinstance(event, RecurrentEvent):
        payload.update(
            {
                "interval": event.interval,
                "interval_end": _timestamp(event.interval_end),
                "recurrence_mode": event.recurrence_mode,
                "recurrence_timezone": event.recurrence_timezone,
            }
        )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _resolve_scope(
    session: AsyncSession,
    actor: User,
    subject_id: uuid.UUID | None,
    *,
    teacher_only: bool,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    if teacher_only and actor.role != Roles.TEACHER:
        raise Forbidden("Only teachers can manage breaks")
    if actor.role == Roles.TEACHER:
        return actor.id, subject_id
    if actor.role != Roles.STUDENT:
        raise Forbidden("Only teachers and students can manage events")
    if actor.teacher_id is None:
        raise BadRequest("Student is not linked to a teacher")
    if subject_id is not None and subject_id != actor.id:
        raise Forbidden("Students can only manage their own events")

    teacher_state = await session.execute(
        select(User.role, User.is_active).where(User.id == actor.teacher_id)
    )
    state = teacher_state.one_or_none()
    if state is None or state.role != Roles.TEACHER or not state.is_active:
        raise Conflict("Linked teacher cannot provide event management")
    return actor.teacher_id, actor.id


async def _management_item(
    session: AsyncSession,
    actor: User,
    teacher_id: uuid.UUID,
    event: Event | RecurrentEvent,
    *,
    is_recurrent: bool,
    cancelled_occurrences: int,
) -> EventManagementItem:
    visibility = None if is_recurrent else await _one_off_visibility(session, event)
    return EventManagementItem(
        id=event.id,
        event_type=EventType(event.event_type),
        start=from_storage_utc(event.start),
        end=from_storage_utc(event.end),
        user_id=event.user_id,
        subject_label=await _subject_label(
            session,
            actor,
            teacher_id,
            event.user_id,
            EventType(event.event_type),
        ),
        is_recurrent=is_recurrent,
        cancelled_occurrences=cancelled_occurrences,
        visibility_status=visibility,
    )


async def _subject_label(
    session: AsyncSession,
    actor: User,
    teacher_id: uuid.UUID,
    subject_id: uuid.UUID,
    event_type: EventType,
) -> str:
    if subject_id == teacher_id:
        return _PERSONAL_SUBJECT_LABELS[event_type]
    if actor.role == Roles.STUDENT and subject_id == actor.id:
        return (actor.full_name or "").strip() or "You"
    full_name = await session.scalar(
        select(User.full_name).where(User.id == subject_id)
    )
    return (full_name or "").strip() or _UNNAMED_STUDENT


async def _one_off_visibility(
    session: AsyncSession, event: Event | RecurrentEvent
) -> VisibilityStatus:
    if event.event_type == EventType.VACATION.value:
        return VisibilityStatus.NOT_APPLICABLE
    return await _visibility_for_interval(
        session,
        event.teacher_id,
        event.user_id,
        event.start,
        event.end,
    )


async def _visibility_for_interval(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    subject_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> VisibilityStatus:
    relevant_subjects = {teacher_id, subject_id}
    overlapping_vacation_id = await session.scalar(
        select(Event.id)
        .where(
            Event.teacher_id == teacher_id,
            Event.event_type == EventType.VACATION.value,
            Event.user_id.in_(relevant_subjects),
            Event.start < end,
            Event.end > start,
        )
        .limit(1)
    )
    return (
        VisibilityStatus.HIDDEN_BY_VACATION
        if overlapping_vacation_id is not None
        else VisibilityStatus.VISIBLE
    )


async def _inspect_occurrence(
    session: AsyncSession,
    event: RecurrentEvent,
    cancellations: list[RecurrentCancels],
    requested_start: datetime,
) -> ManagedOccurrence:
    start = normalize_utc(requested_start)
    try:
        range_end = start + timedelta(microseconds=1)
    except OverflowError as exc:
        raise ValidationError(
            "Occurrence start is outside the supported range"
        ) from exc

    source = OccurrenceSource(
        kind=OccurrenceSourceKind.RECURRENT_EVENT,
        id=event.id,
        teacher_id=event.teacher_id,
        user_id=event.user_id,
        event_type=EventType(event.event_type),
    )
    series = RecurrenceSeries(
        source=source,
        start=event.start,
        end=event.end,
        interval=event.interval,
        interval_end=_require_finite_cutoff(event.interval_end),
        recurrence_mode=event.recurrence_mode,
        recurrence_timezone=event.recurrence_timezone,
    )
    occurrence = next(
        (
            item
            for item in expand_recurrence(series, start, range_end)
            if item.start == start
        ),
        None,
    )
    if occurrence is None:
        raise ValidationError("Occurrence start is not part of this series")

    if any(
        from_storage_utc(cancel.start) == occurrence.start
        and from_storage_utc(cancel.end) == occurrence.end
        for cancel in cancellations
    ):
        status = ManagedOccurrenceStatus.CANCELLED
    elif (
        await _visibility_for_interval(
            session,
            event.teacher_id,
            event.user_id,
            occurrence.start,
            occurrence.end,
        )
        == VisibilityStatus.HIDDEN_BY_VACATION
    ):
        status = ManagedOccurrenceStatus.HIDDEN_BY_VACATION
    else:
        status = ManagedOccurrenceStatus.VISIBLE
    return ManagedOccurrence(
        start=occurrence.start,
        end=occurrence.end,
        status=status,
    )


async def _cancellation_counts(
    session: AsyncSession, event_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not event_ids:
        return {}
    results = await session.execute(
        select(RecurrentCancels.recurrent_event_id, func.count(RecurrentCancels.id))
        .where(RecurrentCancels.recurrent_event_id.in_(event_ids))
        .group_by(RecurrentCancels.recurrent_event_id)
    )
    counts: dict[uuid.UUID, int] = {}
    for event_id, count in results:
        counts[event_id] = count
    return counts


def _require_finite_cutoff(value: datetime | None) -> datetime:
    if value is None:
        raise ValidationError(
            "Open-ended occurrence inspection is not supported by this backend release"
        )
    return value


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return from_storage_utc(value).isoformat()

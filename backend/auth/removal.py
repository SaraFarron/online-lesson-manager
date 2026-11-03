import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, status
from jwt.exceptions import InvalidTokenError
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.config import auth_settings
from backend.auth.constants import Roles
from backend.auth.models import (
    StudentProfile,
    StudentRemovalOperation,
    StudentTeacherRelationship,
    User,
)
from backend.auth.schemas import (
    StudentRemovalConfirm,
    StudentRemovalImpact,
    StudentRemovalPreview,
    StudentRemovalResult,
)
from backend.constants import EventType
from backend.events.models import Event, RecurrentCancels, RecurrentEvent
from backend.events.mutations.serialization import run_serialized_schedule_mutation
from backend.events.occurrences import (
    OccurrenceSource,
    OccurrenceSourceKind,
    RecurrenceSeries,
    expand_recurrence,
)
from backend.events.time_utils import from_storage_utc, normalize_utc, to_storage_utc
from backend.exceptions import Conflict, Forbidden, NotFound
from backend.homework.config import HomeworkConfig
from backend.homework.models import Homework, HomeworkAttachment
from backend.homework.services import attachments as attachment_service
from backend.notifications.constants import NotificationState, NotificationType
from backend.notifications.models import NotificationIntent

logger = logging.getLogger(__name__)
_PREVIEW_TTL = timedelta(minutes=5)
_UNSENT_STATES = (
    NotificationState.PENDING.value,
    NotificationState.RETRY.value,
    NotificationState.CLAIMED.value,
)


@dataclass(frozen=True)
class _SeriesTruncation:
    event_id: uuid.UUID
    interval_end: datetime


@dataclass(frozen=True)
class _RemovalPlan:
    relationship_id: uuid.UUID
    one_off_event_ids: tuple[uuid.UUID, ...]
    series_truncations: tuple[_SeriesTruncation, ...]
    series_deletion_ids: tuple[uuid.UUID, ...]
    cancellation_ids: tuple[uuid.UUID, ...]
    staged_attachment_ids: tuple[uuid.UUID, ...]
    notification_states: tuple[tuple[uuid.UUID, str], ...]

    @property
    def impact(self) -> dict[str, int]:
        return {
            "one_off_lessons_to_delete": len(self.one_off_event_ids),
            "recurring_series_to_truncate": len(self.series_truncations),
            "recurring_series_to_delete": len(self.series_deletion_ids),
            "future_cancellations_to_delete": len(self.cancellation_ids),
            "staged_uploads_to_revoke": len(self.staged_attachment_ids),
            "notifications_to_suppress": len(self.notification_states),
        }

    @property
    def fingerprint(self) -> str:
        snapshot = {
            "relationship_id": str(self.relationship_id),
            "one_off_event_ids": [str(value) for value in self.one_off_event_ids],
            "series_truncations": [
                [str(value.event_id), value.interval_end.isoformat()]
                for value in self.series_truncations
            ],
            "series_deletion_ids": [str(value) for value in self.series_deletion_ids],
            "cancellation_ids": [str(value) for value in self.cancellation_ids],
            "staged_attachment_ids": [
                str(value) for value in self.staged_attachment_ids
            ],
            "notification_states": [
                [str(intent_id), state] for intent_id, state in self.notification_states
            ],
        }
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _require_teacher(teacher: User) -> None:
    if teacher.role != Roles.TEACHER:
        raise Forbidden("Only teachers can remove their own students")


async def _active_relationship(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    student_id: uuid.UUID,
    *,
    lock: bool = False,
) -> StudentTeacherRelationship:
    statement = select(StudentTeacherRelationship).where(
        StudentTeacherRelationship.teacher_id == teacher_id,
        StudentTeacherRelationship.student_id == student_id,
        StudentTeacherRelationship.removed_at.is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    relationship = await session.scalar(statement)
    if relationship is None:
        raise NotFound("Active student relationship not found")
    return relationship


def _recurrence_series(event: RecurrentEvent) -> RecurrenceSeries:
    source = OccurrenceSource(
        kind=OccurrenceSourceKind.RECURRENT_EVENT,
        id=event.id,
        teacher_id=event.teacher_id,
        user_id=event.user_id,
        event_type=EventType(event.event_type),
        post_break_minutes=event.post_break_minutes,
    )
    return RecurrenceSeries(
        source=source,
        start=from_storage_utc(event.start),
        end=from_storage_utc(event.end),
        interval=event.interval,
        interval_end=(
            from_storage_utc(event.interval_end)
            if event.interval_end is not None
            else None
        ),
        recurrence_mode=event.recurrence_mode,
        recurrence_timezone=event.recurrence_timezone,
    )


def _last_ended_occurrence(
    series: RecurrenceSeries, cutoff: datetime
) -> datetime | None:
    if series.end > cutoff:
        return None
    duration = series.end - series.start
    step = timedelta(days=series.interval)
    try:
        range_start = max(
            series.start,
            cutoff - duration - step - timedelta(days=3),
        )
        range_end = cutoff + timedelta(microseconds=1)
    except OverflowError as exc:
        raise Conflict(
            "Recurring lesson dates are outside the supported range"
        ) from exc

    occurrences = expand_recurrence(series, range_start, range_end)
    ended = [occurrence for occurrence in occurrences if occurrence.end <= cutoff]
    if not ended:
        return None
    return max(ended, key=lambda occurrence: occurrence.start).start


async def _relationship_notification_states(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    student_id: uuid.UUID,
) -> tuple[tuple[uuid.UUID, str], ...]:
    homework_ids = select(Homework.id).where(
        Homework.teacher_id == teacher_id,
        Homework.student_id == student_id,
    )
    event_ids = select(Event.id).where(
        Event.teacher_id == teacher_id,
        Event.user_id == student_id,
        Event.event_type == EventType.LESSON.value,
    )
    recurrent_event_ids = select(RecurrentEvent.id).where(
        RecurrentEvent.teacher_id == teacher_id,
        RecurrentEvent.user_id == student_id,
        RecurrentEvent.event_type == EventType.LESSON.value,
    )
    scoped_intent = or_(
        NotificationIntent.recipient_id == student_id,
        and_(
            NotificationIntent.recipient_id == teacher_id,
            NotificationIntent.notification_type
            == NotificationType.SUBMISSION_RECEIVED.value,
            NotificationIntent.source_id.in_(homework_ids),
        ),
        and_(
            NotificationIntent.recipient_id == teacher_id,
            NotificationIntent.occurrence_source_type == "event",
            NotificationIntent.occurrence_source_id.in_(event_ids),
        ),
        and_(
            NotificationIntent.recipient_id == teacher_id,
            NotificationIntent.occurrence_source_type == "recurrent_event",
            NotificationIntent.occurrence_source_id.in_(recurrent_event_ids),
        ),
    )
    rows = await session.execute(
        select(NotificationIntent.id, NotificationIntent.state)
        .where(
            NotificationIntent.state.in_(_UNSENT_STATES),
            scoped_intent,
        )
        .order_by(NotificationIntent.id)
    )
    return tuple((intent_id, state) for intent_id, state in rows.all())


async def _build_removal_plan(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    student_id: uuid.UUID,
    relationship_id: uuid.UUID,
    cutoff: datetime,
) -> _RemovalPlan:
    stored_cutoff = to_storage_utc(cutoff)
    one_off_event_ids = tuple(
        await session.scalars(
            select(Event.id)
            .where(
                Event.teacher_id == teacher_id,
                Event.user_id == student_id,
                Event.event_type == EventType.LESSON.value,
                Event.end > stored_cutoff,
            )
            .order_by(Event.id)
        )
    )

    recurrent_events = list(
        await session.scalars(
            select(RecurrentEvent)
            .where(
                RecurrentEvent.teacher_id == teacher_id,
                RecurrentEvent.user_id == student_id,
                RecurrentEvent.event_type == EventType.LESSON.value,
            )
            .order_by(RecurrentEvent.id)
        )
    )
    truncations: list[_SeriesTruncation] = []
    deletion_ids: list[uuid.UUID] = []
    cancellation_ids: list[uuid.UUID] = []
    current_range_end = cutoff + timedelta(microseconds=1)
    for event in recurrent_events:
        series = _recurrence_series(event)
        current_occurrences = expand_recurrence(series, cutoff, current_range_end)
        has_in_progress_occurrence = any(
            occurrence.start <= cutoff and occurrence.end > cutoff
            for occurrence in current_occurrences
        )
        has_future_series = series.interval_end is None or series.interval_end > cutoff
        if not has_future_series and not has_in_progress_occurrence:
            continue

        last_ended_start = _last_ended_occurrence(series, cutoff)
        if last_ended_start is None:
            deletion_ids.append(event.id)
            cancellation_ids.extend(
                await session.scalars(
                    select(RecurrentCancels.id).where(
                        RecurrentCancels.recurrent_event_id == event.id
                    )
                )
            )
            continue

        new_interval_end = to_storage_utc(last_ended_start + timedelta(microseconds=1))
        truncations.append(_SeriesTruncation(event.id, new_interval_end))
        cancellation_ids.extend(
            await session.scalars(
                select(RecurrentCancels.id).where(
                    RecurrentCancels.recurrent_event_id == event.id,
                    RecurrentCancels.start >= new_interval_end,
                )
            )
        )

    staged_attachment_ids = tuple(
        await session.scalars(
            select(HomeworkAttachment.id)
            .where(
                HomeworkAttachment.owner_user_id == student_id,
                HomeworkAttachment.homework_id.is_(None),
                HomeworkAttachment.submission_id.is_(None),
            )
            .order_by(HomeworkAttachment.id)
        )
    )
    notification_states = await _relationship_notification_states(
        session, teacher_id, student_id
    )
    return _RemovalPlan(
        relationship_id=relationship_id,
        one_off_event_ids=one_off_event_ids,
        series_truncations=tuple(truncations),
        series_deletion_ids=tuple(deletion_ids),
        cancellation_ids=tuple(sorted(set(cancellation_ids))),
        staged_attachment_ids=staged_attachment_ids,
        notification_states=notification_states,
    )


def _encode_preview(
    teacher_id: uuid.UUID,
    student_id: uuid.UUID,
    plan: _RemovalPlan,
    expires_at: datetime,
) -> str:
    return jwt.encode(
        {
            "purpose": "student-removal",
            "teacher_id": str(teacher_id),
            "student_id": str(student_id),
            "relationship_id": str(plan.relationship_id),
            "fingerprint": plan.fingerprint,
            "jti": uuid.uuid4().hex,
            "exp": int(expires_at.timestamp()),
        },
        auth_settings.JWT_SECRET,
        algorithm=auth_settings.JWT_ALG,
    )


def _decode_preview(token: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            auth_settings.JWT_SECRET,
            algorithms=[auth_settings.JWT_ALG],
            options={"verify_exp": False},
        )
    except InvalidTokenError as exc:
        raise Conflict("Removal preview is invalid; request a new preview") from exc
    if (
        claims.get("purpose") != "student-removal"
        or not isinstance(claims.get("fingerprint"), str)
        or not isinstance(claims.get("exp"), int)
        or not isinstance(claims.get("jti"), str)
    ):
        raise Conflict("Removal preview is invalid; request a new preview")
    try:
        uuid.UUID(claims["teacher_id"])
        uuid.UUID(claims["student_id"])
        uuid.UUID(claims["relationship_id"])
    except (KeyError, ValueError, TypeError) as exc:
        raise Conflict("Removal preview is invalid; request a new preview") from exc
    return claims


async def preview_student_removal(
    session: AsyncSession,
    teacher: User,
    student_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> StudentRemovalPreview:
    _require_teacher(teacher)
    timestamp = normalize_utc(now or datetime.now(UTC))
    student = await session.scalar(
        select(User).where(
            User.id == student_id,
            User.role == Roles.STUDENT,
            User.is_active.is_(True),
            User.teacher_id == teacher.id,
        )
    )
    if student is None:
        raise NotFound("Active student relationship not found")
    relationship = await _active_relationship(session, teacher.id, student_id)
    plan = await _build_removal_plan(
        session, teacher.id, student_id, relationship.id, timestamp
    )
    expires_at = datetime.fromtimestamp(
        int((timestamp + _PREVIEW_TTL).timestamp()), UTC
    )
    return StudentRemovalPreview(
        student_id=student_id,
        relationship_id=relationship.id,
        impact=StudentRemovalImpact(**plan.impact),
        confirmation_token=_encode_preview(teacher.id, student_id, plan, expires_at),
        expires_at=expires_at,
    )


def _removal_result(operation: StudentRemovalOperation) -> StudentRemovalResult:
    return StudentRemovalResult(
        student_id=operation.student_id,
        relationship_id=operation.relationship_id,
        operation_key=operation.operation_key,
        removed_at=normalize_utc(operation.removed_at),
        impact=StudentRemovalImpact(**operation.impact),
    )


def _operation_matches(
    operation: StudentRemovalOperation,
    teacher_id: uuid.UUID,
    student_id: uuid.UUID,
    request: StudentRemovalConfirm,
) -> bool:
    return (
        operation.teacher_id == teacher_id
        and operation.student_id == student_id
        and operation.confirmation_token == request.preview_token
    )


async def _reconcile_operation(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    student_id: uuid.UUID,
    request: StudentRemovalConfirm,
) -> StudentRemovalOperation | None:
    try:
        result = await session.execute(
            select(StudentRemovalOperation).where(
                StudentRemovalOperation.teacher_id == teacher_id,
                StudentRemovalOperation.operation_key == request.operation_key,
                StudentRemovalOperation.student_id == student_id,
                StudentRemovalOperation.confirmation_token == request.preview_token,
            )
        )
        return result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        logger.exception("Unable to reconcile student removal operation")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Removal outcome is unknown; retry with the same operation key",
        ) from exc


async def _confirm_removal_transaction(
    session: AsyncSession,
    teacher: User,
    student_id: uuid.UUID,
    request: StudentRemovalConfirm,
    claims: dict[str, Any],
) -> StudentRemovalOperation:
    operation = await session.scalar(
        select(StudentRemovalOperation).where(
            StudentRemovalOperation.teacher_id == teacher.id,
            StudentRemovalOperation.operation_key == request.operation_key,
        )
    )
    if operation is not None:
        if not _operation_matches(operation, teacher.id, student_id, request):
            raise Conflict("Operation key was already used for another removal")
        await session.commit()
        return operation

    reused_token = await session.scalar(
        select(StudentRemovalOperation.id).where(
            StudentRemovalOperation.confirmation_token == request.preview_token
        )
    )
    if reused_token is not None:
        raise Conflict("Removal preview was already confirmed")

    timestamp = datetime.now(UTC)
    if claims["exp"] <= int(timestamp.timestamp()):
        raise Conflict("Removal preview expired; request a new preview")
    if claims["teacher_id"] != str(teacher.id) or claims["student_id"] != str(
        student_id
    ):
        raise Forbidden("Removal preview belongs to another teacher or student")

    # Lock order: teacher schedule guard -> student -> relationship -> staged files
    # -> notification intents. Homework writers acquire the guard before student
    # and homework rows.
    student = await session.scalar(
        select(User)
        .where(
            User.id == student_id,
            User.role == Roles.STUDENT,
            User.is_active.is_(True),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if student is None or student.teacher_id != teacher.id:
        raise Conflict("Student link changed; request a new removal preview")
    relationship = await _active_relationship(
        session, teacher.id, student_id, lock=True
    )
    if str(relationship.id) != claims["relationship_id"]:
        raise Conflict("Student link changed; request a new removal preview")

    plan = await _build_removal_plan(
        session, teacher.id, student_id, relationship.id, timestamp
    )
    if plan.fingerprint != claims["fingerprint"]:
        raise Conflict("Student data changed; request a new removal preview")

    if (
        relationship.linked_at is not None
        and normalize_utc(relationship.linked_at) > timestamp
    ):
        raise Conflict("Student relationship has an invalid future link date")

    if plan.one_off_event_ids:
        await session.execute(delete(Event).where(Event.id.in_(plan.one_off_event_ids)))
    for truncation in plan.series_truncations:
        await session.execute(
            update(RecurrentEvent)
            .where(
                RecurrentEvent.id == truncation.event_id,
                RecurrentEvent.teacher_id == teacher.id,
                RecurrentEvent.user_id == student_id,
            )
            .values(
                interval_end=truncation.interval_end,
                updated_at=timestamp,
            )
        )
    if plan.cancellation_ids:
        await session.execute(
            delete(RecurrentCancels).where(
                RecurrentCancels.id.in_(plan.cancellation_ids)
            )
        )
    if plan.series_deletion_ids:
        await session.execute(
            delete(RecurrentEvent).where(
                RecurrentEvent.id.in_(plan.series_deletion_ids),
                RecurrentEvent.teacher_id == teacher.id,
                RecurrentEvent.user_id == student_id,
            )
        )

    if plan.staged_attachment_ids:
        await session.execute(
            update(HomeworkAttachment)
            .where(
                HomeworkAttachment.id.in_(plan.staged_attachment_ids),
                HomeworkAttachment.owner_user_id == student_id,
                HomeworkAttachment.homework_id.is_(None),
                HomeworkAttachment.submission_id.is_(None),
            )
            .values(cleanup_pending=True, updated_at=timestamp)
        )

    if plan.notification_states:
        await session.execute(
            update(NotificationIntent)
            .where(
                NotificationIntent.id.in_(
                    [intent_id for intent_id, _ in plan.notification_states]
                ),
                NotificationIntent.state.in_(_UNSENT_STATES),
            )
            .values(
                state=NotificationState.SUPPRESSED.value,
                lease_token=None,
                lease_expires_at=None,
                last_outcome="TERMINAL",
                last_error_code="student_relationship_removed",
                updated_at=timestamp,
            )
        )

    student.teacher_id = None
    student.notification_schedule_updated_at = timestamp
    relationship.removed_at = timestamp
    relationship.updated_at = timestamp
    profile = await session.get(StudentProfile, student_id)
    if profile is not None:
        profile.notification_lesson_updated_at = timestamp
        profile.notification_homework_updated_at = timestamp

    operation = StudentRemovalOperation(
        relationship_id=relationship.id,
        teacher_id=teacher.id,
        student_id=student_id,
        operation_key=request.operation_key,
        confirmation_token=request.preview_token,
        impact=plan.impact,
        staged_attachment_ids=[str(value) for value in plan.staged_attachment_ids],
        removed_at=timestamp,
    )
    session.add(operation)
    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        reconciled = await _reconcile_operation(
            session, teacher.id, student_id, request
        )
        if reconciled is not None:
            return reconciled
        logger.exception("Student removal commit outcome is unknown")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Removal outcome is unknown; retry with the same operation key",
        ) from exc
    return operation


async def _finish_staged_file_cleanup(
    session: AsyncSession,
    operation: StudentRemovalOperation,
    config: HomeworkConfig,
) -> None:
    for attachment_id in operation.staged_attachment_ids:
        try:
            await attachment_service.cleanup_staged_attachment(
                session,
                operation.student_id,
                uuid.UUID(attachment_id),
                config,
            )
        except HTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
            remaining = await session.get(HomeworkAttachment, uuid.UUID(attachment_id))
            if remaining is not None:
                raise


async def confirm_student_removal(
    session: AsyncSession,
    teacher: User,
    student_id: uuid.UUID,
    request: StudentRemovalConfirm,
    config: HomeworkConfig,
) -> StudentRemovalResult:
    _require_teacher(teacher)
    claims = _decode_preview(request.preview_token)
    operation = await run_serialized_schedule_mutation(
        session,
        teacher.id,
        lambda: _confirm_removal_transaction(
            session, teacher, student_id, request, claims
        ),
        rollback_domain_errors=True,
    )
    await _finish_staged_file_cleanup(session, operation, config)
    return _removal_result(operation)

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Literal

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from backend.auth.constants import Roles
from backend.auth.models import User
from backend.constants import EventType
from backend.events.time_utils import (
    get_timezone,
    normalize_utc,
    resolve_local_datetime,
    to_storage_utc,
)
from backend.exceptions import BadRequest, Conflict, Forbidden, ValidationError
from backend.homework.models import Homework, HomeworkSubmission
from backend.notifications.constants import (
    CLAIM_LEASE_SECONDS,
    MAX_CLAIM_BATCH,
    MAX_DELIVERY_ATTEMPTS,
    PUSH_CATCH_UP,
    AcknowledgementOutcome,
    NotificationState,
    NotificationType,
)
from backend.notifications.content import build_notification_content
from backend.notifications.models import NotificationIntent
from backend.notifications.schemas import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    ClaimedNotification,
    NotificationHealth,
)
from backend.schedule import service as schedule_service
from backend.schedule.schemas import ScheduleItem, SchedulePeriod


def add_assignment_created_intent(
    session: AsyncSession,
    homework: Homework,
    student: User,
    *,
    now: datetime | None = None,
) -> None:
    profile = student.student_profile
    if (
        not student.is_active
        or student.telegram_private_chat_id is None
        or profile is None
        or not profile.notification_homework_assignment
    ):
        return
    _add_homework_push_intent(
        session,
        recipient=student,
        notification_type=NotificationType.ASSIGNMENT_CREATED,
        homework=homework,
        now=now,
    )


def add_submission_received_intent(
    session: AsyncSession,
    homework: Homework,
    submission: HomeworkSubmission,
    teacher: User,
    *,
    now: datetime | None = None,
) -> None:
    profile = teacher.teacher_profile
    if (
        not teacher.is_active
        or teacher.role != Roles.TEACHER
        or teacher.telegram_private_chat_id is None
        or profile is None
        or not profile.notification_homework_submission
    ):
        return
    _add_homework_push_intent(
        session,
        recipient=teacher,
        notification_type=NotificationType.SUBMISSION_RECEIVED,
        homework=homework,
        source_version=submission.version,
        now=now,
    )


def _add_homework_push_intent(
    session: AsyncSession,
    *,
    recipient: User,
    notification_type: NotificationType,
    homework: Homework,
    source_version: int | None = None,
    now: datetime | None = None,
) -> None:
    timestamp = normalize_utc(now or datetime.now(UTC))
    assignment = notification_type == NotificationType.ASSIGNMENT_CREATED
    dedupe_key = (
        f"assignment:{recipient.id}:{homework.id}"
        if assignment
        else f"submission:{recipient.id}:{homework.id}:{source_version}"
    )
    session.add(
        NotificationIntent(
            id=uuid.uuid4(),
            recipient_id=recipient.id,
            notification_type=notification_type.value,
            dedupe_key=dedupe_key,
            source_id=homework.id,
            source_version=source_version,
            scheduled_at=timestamp,
            available_at=timestamp,
            expires_at=timestamp + PUSH_CATCH_UP,
            state=NotificationState.PENDING,
            attempt_count=0,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )


async def claim_pending_intents(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = MAX_CLAIM_BATCH,
) -> list[ClaimedNotification]:
    timestamp = normalize_utc(now or datetime.now(UTC))
    batch_size = min(max(limit, 1), MAX_CLAIM_BATCH)
    await _recover_expired_intents(session, timestamp)

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        intents = await _claim_postgres(session, timestamp, batch_size)
    elif dialect == "sqlite":
        intents = await _claim_sqlite(session, timestamp, batch_size)
    else:
        raise RuntimeError(f"Unsupported notification claim database: {dialect}")

    recipients = await _load_recipients(session, intents)
    active_teacher_ids = await _load_active_teacher_ids(session, recipients.values())
    claimed: list[ClaimedNotification] = []
    for intent in intents:
        user = recipients.get(intent.recipient_id)
        reason = _recipient_suppression_reason(user, intent, active_teacher_ids)
        if reason is None:
            reason = await _source_suppression_reason(session, user, intent, timestamp)
        if reason is not None:
            _suppress(intent, timestamp, reason)
            continue
        if intent.lease_token is None or intent.lease_expires_at is None:
            raise RuntimeError("Claimed notification is missing lease state")
        occurrence_source_type: Literal["event", "recurrent_event"] | None
        if intent.occurrence_source_type == "event":
            occurrence_source_type = "event"
        elif intent.occurrence_source_type == "recurrent_event":
            occurrence_source_type = "recurrent_event"
        elif intent.occurrence_source_type is None:
            occurrence_source_type = None
        else:
            _suppress(intent, timestamp, "occurrence_source_invalid")
            continue
        if user is None or user.telegram_private_chat_id is None:
            raise RuntimeError("Deliverable notification has no Telegram route")
        content = await build_notification_content(session, user, intent, timestamp)
        claimed.append(
            ClaimedNotification(
                id=intent.id,
                recipient_id=intent.recipient_id,
                chat_id=user.telegram_private_chat_id,
                notification_type=NotificationType(intent.notification_type),
                content=content,
                source_id=intent.source_id,
                source_version=intent.source_version,
                source_date=intent.source_date,
                occurrence_source_type=occurrence_source_type,
                occurrence_source_id=intent.occurrence_source_id,
                occurrence_start_at=intent.occurrence_start_at,
                scheduled_at=normalize_utc(intent.scheduled_at),
                lease_token=intent.lease_token,
                lease_expires_at=normalize_utc(intent.lease_expires_at),
            )
        )
    await session.commit()
    return claimed


async def _recover_expired_intents(session: AsyncSession, now: datetime) -> None:
    await session.execute(
        update(NotificationIntent)
        .where(
            NotificationIntent.state.in_(
                (NotificationState.PENDING, NotificationState.RETRY)
            ),
            NotificationIntent.expires_at <= now,
        )
        .values(
            state=NotificationState.SUPPRESSED,
            last_outcome=AcknowledgementOutcome.TERMINAL.name,
            last_error_code="expired",
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(NotificationIntent)
        .where(
            NotificationIntent.state.in_(
                (NotificationState.PENDING, NotificationState.RETRY)
            ),
            NotificationIntent.attempt_count >= MAX_DELIVERY_ATTEMPTS,
        )
        .values(
            state=NotificationState.TERMINAL,
            last_outcome=AcknowledgementOutcome.TERMINAL.name,
            last_error_code="attempts_exhausted",
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(NotificationIntent)
        .where(
            NotificationIntent.state == NotificationState.CLAIMED,
            NotificationIntent.lease_expires_at <= now,
        )
        .values(
            state=case(
                (NotificationIntent.expires_at <= now, NotificationState.SUPPRESSED),
                (
                    NotificationIntent.attempt_count >= MAX_DELIVERY_ATTEMPTS,
                    NotificationState.TERMINAL,
                ),
                else_=NotificationState.RETRY,
            ),
            available_at=now,
            lease_token=None,
            lease_expires_at=None,
            last_outcome=AcknowledgementOutcome.UNKNOWN.name,
            last_error_code=case(
                (NotificationIntent.expires_at <= now, "expired"),
                (
                    NotificationIntent.attempt_count >= MAX_DELIVERY_ATTEMPTS,
                    "attempts_exhausted",
                ),
                else_="lease_expired",
            ),
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )


def _due_intents(now: datetime, limit: int) -> Select[tuple[NotificationIntent]]:
    return (
        select(NotificationIntent)
        .where(
            NotificationIntent.state.in_(
                (NotificationState.PENDING, NotificationState.RETRY)
            ),
            NotificationIntent.available_at <= now,
            NotificationIntent.expires_at > now,
            NotificationIntent.attempt_count < MAX_DELIVERY_ATTEMPTS,
        )
        .order_by(
            NotificationIntent.available_at,
            NotificationIntent.created_at,
            NotificationIntent.id,
        )
        .limit(limit)
    )


async def _claim_postgres(
    session: AsyncSession, now: datetime, limit: int
) -> list[NotificationIntent]:
    intents = list(
        (
            await session.scalars(
                _due_intents(now, limit).with_for_update(skip_locked=True)
            )
        ).all()
    )
    for intent in intents:
        intent.state = NotificationState.CLAIMED
        intent.attempt_count += 1
        intent.lease_token = uuid.uuid4()
        intent.lease_expires_at = now + timedelta(seconds=CLAIM_LEASE_SECONDS)
        intent.updated_at = now
    await session.flush()
    return intents


async def _claim_sqlite(
    session: AsyncSession, now: datetime, limit: int
) -> list[NotificationIntent]:
    token = uuid.uuid4()
    result = await session.execute(
        update(NotificationIntent)
        .where(
            NotificationIntent.id.in_(
                _due_intents(now, limit).with_only_columns(NotificationIntent.id)
            ),
            NotificationIntent.state.in_(
                (NotificationState.PENDING, NotificationState.RETRY)
            ),
        )
        .values(
            state=NotificationState.CLAIMED,
            attempt_count=NotificationIntent.attempt_count + 1,
            lease_token=token,
            lease_expires_at=now + timedelta(seconds=CLAIM_LEASE_SECONDS),
            updated_at=now,
        )
        .returning(NotificationIntent)
    )
    return list(result.scalars().all())


async def _load_recipients(
    session: AsyncSession, intents: list[NotificationIntent]
) -> dict[uuid.UUID, User]:
    recipient_ids = {intent.recipient_id for intent in intents}
    if not recipient_ids:
        return {}
    users = await session.scalars(
        select(User)
        .where(User.id.in_(recipient_ids))
        .options(selectinload(User.student_profile), selectinload(User.teacher_profile))
    )
    return {user.id: user for user in users}


async def _load_active_teacher_ids(
    session: AsyncSession, users: Iterable[User]
) -> set[uuid.UUID]:
    teacher_ids = {
        user.teacher_id
        for user in users
        if user.role == Roles.STUDENT and user.teacher_id is not None
    }
    if not teacher_ids:
        return set()
    return set(
        await session.scalars(
            select(User.id).where(
                User.id.in_(teacher_ids),
                User.role == Roles.TEACHER,
                User.is_active.is_(True),
            )
        )
    )


def _recipient_suppression_reason(
    user: User | None,
    intent: NotificationIntent,
    active_teacher_ids: set[uuid.UUID],
) -> str | None:
    if user is None:
        return "recipient_missing"
    if not user.is_active:
        return "recipient_inactive"
    if user.telegram_private_chat_id is None:
        return "recipient_unroutable"
    if user.role == Roles.STUDENT:
        if user.teacher_id not in active_teacher_ids:
            return "student_link_inactive"
    elif user.role != Roles.TEACHER:
        return "recipient_role_invalid"

    kind = NotificationType(intent.notification_type)
    if kind == NotificationType.DAILY_DIGEST:
        if user.notification_lessons_today is None:
            return "digest_disabled"
    elif kind == NotificationType.LESSON_REMINDER:
        if (
            user.role != Roles.STUDENT
            or user.student_profile is None
            or user.student_profile.notification_lesson is None
        ):
            return "lesson_reminder_disabled"
    elif kind == NotificationType.HOMEWORK_REMINDER:
        if (
            user.role != Roles.STUDENT
            or user.student_profile is None
            or user.student_profile.notification_homework is None
            or user.notification_lessons_today is None
        ):
            return "homework_reminder_disabled"
    elif kind == NotificationType.ASSIGNMENT_CREATED:
        if (
            user.role != Roles.STUDENT
            or user.student_profile is None
            or not user.student_profile.notification_homework_assignment
        ):
            return "assignment_notification_disabled"
    elif kind == NotificationType.SUBMISSION_RECEIVED:
        if (
            user.role != Roles.TEACHER
            or user.teacher_profile is None
            or not user.teacher_profile.notification_homework_submission
        ):
            return "submission_notification_disabled"
    return None


async def _source_suppression_reason(
    session: AsyncSession,
    user: User | None,
    intent: NotificationIntent,
    now: datetime,
) -> str | None:
    if user is None:
        return "recipient_missing"
    kind = NotificationType(intent.notification_type)
    if kind in {NotificationType.DAILY_DIGEST, NotificationType.LESSON_REMINDER}:
        schedule_date = (
            intent.source_date
            if kind == NotificationType.DAILY_DIGEST
            else (
                intent.occurrence_start_at.astimezone(
                    get_timezone(user.timezone)
                ).date()
                if intent.occurrence_start_at is not None
                else None
            )
        )
        if schedule_date is None:
            return "schedule_reference_missing"
        if kind == NotificationType.DAILY_DIGEST and (
            schedule_date != now.astimezone(get_timezone(user.timezone)).date()
        ):
            return "digest_date_stale"
        try:
            schedule = await schedule_service.get_schedule(
                session, user, SchedulePeriod.DAY, schedule_date, now
            )
        except BadRequest, Conflict, Forbidden, ValidationError:
            return "schedule_unavailable"
        if kind == NotificationType.DAILY_DIGEST:
            return (
                None
                if any(
                    item.event_type == EventType.LESSON
                    for item in schedule.days[0].items
                )
                else "empty_digest"
            )
        return _match_schedule_occurrence(schedule.days[0].items, intent, now)

    if kind in {
        NotificationType.HOMEWORK_REMINDER,
        NotificationType.ASSIGNMENT_CREATED,
        NotificationType.SUBMISSION_RECEIVED,
    }:
        return await _validate_homework_source(session, user, intent, now)
    return "notification_type_invalid"


def _match_schedule_occurrence(
    items: Sequence[ScheduleItem], intent: NotificationIntent, now: datetime
) -> str | None:
    if (
        intent.occurrence_source_type not in {"event", "recurrent_event"}
        or intent.occurrence_source_id is None
        or intent.occurrence_start_at is None
    ):
        return "schedule_reference_missing"
    source_start = normalize_utc(intent.occurrence_start_at)
    match = next(
        (
            item
            for item in items
            if item.source_kind.value == intent.occurrence_source_type
            and item.source_id == intent.occurrence_source_id
            and normalize_utc(item.start) == source_start
            and item.event_type == EventType.LESSON
        ),
        None,
    )
    if match is None:
        return "lesson_occurrence_stale"
    if normalize_utc(match.end) <= now:
        return "lesson_occurrence_ended"
    if normalize_utc(match.start) <= now:
        return "lesson_occurrence_started"
    return None


async def _validate_homework_source(
    session: AsyncSession,
    recipient: User,
    intent: NotificationIntent,
    now: datetime,
) -> str | None:
    if intent.source_id is None:
        return "homework_reference_missing"
    homework = await session.scalar(
        select(Homework).where(Homework.id == intent.source_id)
    )
    if homework is None or homework.deleted_at is not None or homework.cleanup_pending:
        return "homework_unavailable"
    student = await session.get(User, homework.student_id)
    teacher = await session.get(User, homework.teacher_id)
    if (
        student is None
        or teacher is None
        or not student.is_active
        or not teacher.is_active
        or student.role != Roles.STUDENT
        or teacher.role != Roles.TEACHER
        or student.teacher_id != teacher.id
    ):
        return "homework_link_inactive"

    kind = NotificationType(intent.notification_type)
    if kind == NotificationType.ASSIGNMENT_CREATED:
        return None if recipient.id == student.id else "assignment_recipient_mismatch"
    if kind == NotificationType.SUBMISSION_RECEIVED:
        if recipient.id != teacher.id or intent.source_version is None:
            return "submission_recipient_mismatch"
        submission = await session.scalar(
            select(HomeworkSubmission).where(
                HomeworkSubmission.homework_id == homework.id,
                HomeworkSubmission.version == intent.source_version,
                HomeworkSubmission.student_id == student.id,
            )
        )
        return None if submission is not None else "submission_version_missing"
    if kind != NotificationType.HOMEWORK_REMINDER:
        return "notification_type_invalid"
    if homework.status != "INCOMPLETE":
        return "homework_complete"
    if (
        recipient.id != student.id
        or homework.lesson_kind not in {"EVENT", "RECURRENT_EVENT"}
        or homework.lesson_source_id is None
        or homework.lesson_occurrence_start is None
        or intent.occurrence_source_type
        != ("event" if homework.lesson_kind == "EVENT" else "recurrent_event")
        or intent.occurrence_source_id != homework.lesson_source_id
        or intent.occurrence_start_at is None
        or to_storage_utc(normalize_utc(intent.occurrence_start_at))
        != to_storage_utc(normalize_utc(homework.lesson_occurrence_start))
    ):
        return "homework_lesson_mismatch"
    profile = recipient.student_profile
    if (
        profile is None
        or profile.notification_homework is None
        or recipient.notification_lessons_today is None
    ):
        return "homework_reminder_disabled"
    if normalize_utc(profile.notification_homework_updated_at) > normalize_utc(
        intent.scheduled_at
    ) or normalize_utc(recipient.notification_schedule_updated_at) > normalize_utc(
        intent.scheduled_at
    ):
        return "homework_reminder_preference_changed"
    lesson_start = normalize_utc(homework.lesson_occurrence_start)
    lesson_date = lesson_start.astimezone(get_timezone(student.timezone)).date()
    try:
        reminder_date = lesson_date - timedelta(days=profile.notification_homework)
        reminder_due_at = normalize_utc(
            resolve_local_datetime(
                datetime.combine(
                    reminder_date,
                    time(
                        recipient.notification_lessons_today // 60,
                        recipient.notification_lessons_today % 60,
                    ),
                ),
                student.timezone,
            )
        )
    except OverflowError, ValidationError:
        return "homework_reminder_preference_invalid"
    if intent.source_date != reminder_date or reminder_due_at != normalize_utc(
        intent.scheduled_at
    ):
        return "homework_reminder_preference_changed"
    schedule_date = lesson_start.astimezone(get_timezone(student.timezone)).date()
    try:
        schedule = await schedule_service.get_schedule(
            session, student, SchedulePeriod.DAY, schedule_date, now
        )
    except BadRequest, Conflict, Forbidden, ValidationError:
        return "homework_lesson_unavailable"
    return _match_schedule_occurrence(schedule.days[0].items, intent, now)


def _suppress(intent: NotificationIntent, now: datetime, reason: str) -> None:
    intent.state = NotificationState.SUPPRESSED
    intent.lease_token = None
    intent.lease_expires_at = None
    intent.last_outcome = AcknowledgementOutcome.TERMINAL.name
    intent.last_error_code = reason[:64]
    intent.updated_at = now


async def acknowledge_intent(
    session: AsyncSession,
    intent_id: uuid.UUID,
    request: AcknowledgeRequest,
    *,
    now: datetime | None = None,
) -> AcknowledgeResponse:
    timestamp = normalize_utc(now or datetime.now(UTC))
    statement = select(NotificationIntent).where(
        NotificationIntent.id == intent_id,
        NotificationIntent.state == NotificationState.CLAIMED,
        NotificationIntent.lease_token == request.lease_token,
    )
    if session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update()
    intent = await session.scalar(statement)
    if intent is None or intent.lease_expires_at is None:
        raise Conflict("notification_lease_stale")
    if normalize_utc(intent.lease_expires_at) <= timestamp:
        raise Conflict("notification_lease_stale")

    outcome = request.outcome
    state: NotificationState
    available_at = timestamp
    error_code = request.error_code
    if outcome == AcknowledgementOutcome.SENT:
        state = NotificationState.SENT
        intent.sent_at = timestamp
    elif outcome == AcknowledgementOutcome.TERMINAL:
        state = NotificationState.TERMINAL
    elif intent.attempt_count >= MAX_DELIVERY_ATTEMPTS:
        state = NotificationState.TERMINAL
    else:
        backoff_seconds = 2 ** max(0, intent.attempt_count - 1)
        retry_seconds = max(backoff_seconds, request.retry_after_seconds or 0)
        available_at = timestamp + timedelta(seconds=retry_seconds)
        if available_at >= normalize_utc(intent.expires_at):
            state = NotificationState.TERMINAL
            error_code = "expired"
        else:
            state = NotificationState.RETRY
    intent.state = state
    intent.available_at = available_at
    intent.lease_token = None
    intent.lease_expires_at = None
    intent.last_outcome = outcome.name
    intent.last_error_code = error_code
    intent.updated_at = timestamp
    await session.commit()
    return AcknowledgeResponse(
        id=intent.id,
        state=state,
        last_outcome=outcome,
        available_at=available_at,
    )


async def get_health_counts(session: AsyncSession) -> NotificationHealth:
    results = await session.execute(
        select(NotificationIntent.state, func.count()).group_by(
            NotificationIntent.state
        )
    )
    counts = {str(state): count for state, count in results.all()}
    return NotificationHealth(
        pending=counts.get(NotificationState.PENDING, 0),
        claimed=counts.get(NotificationState.CLAIMED, 0),
        retry=counts.get(NotificationState.RETRY, 0),
        sent=counts.get(NotificationState.SENT, 0),
        terminal=counts.get(NotificationState.TERMINAL, 0),
        suppressed=counts.get(NotificationState.SUPPRESSED, 0),
    )

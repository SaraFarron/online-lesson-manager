import hashlib
import json
import uuid
from datetime import UTC, datetime

import jwt
from jwt.exceptions import InvalidTokenError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import service as auth_service
from backend.auth.config import auth_settings
from backend.auth.constants import Roles
from backend.auth.models import User
from backend.events.time_utils import normalize_utc
from backend.exceptions import Conflict, Forbidden, NotFound
from backend.notifications.constants import (
    PUSH_CATCH_UP,
    AcknowledgementOutcome,
    NotificationState,
    NotificationType,
)
from backend.notifications.models import NotificationIntent, TeacherBroadcast
from backend.notifications.schemas import (
    BroadcastDeliveryStatus,
    TeacherBroadcastPreview,
    TeacherBroadcastPublication,
    TeacherBroadcastPublishRequest,
    TeacherBroadcastRecipientResult,
    TeacherBroadcastResultCounts,
    TeacherBroadcastResults,
)


async def preview_teacher_broadcast(
    session: AsyncSession,
    teacher: User,
    text: str,
) -> TeacherBroadcastPreview:
    _require_teacher(teacher)
    students = await auth_service.get_teacher_student_roster(session, teacher.id)
    token = _confirmation_token(teacher.id, text, students)
    eligible_count = sum(
        student.telegram_private_chat_id is not None for student in students
    )
    unsendable_count = len(students) - eligible_count
    return TeacherBroadcastPreview(
        preview_token=token,
        recipient_count=len(students),
        eligible_count=eligible_count,
        unsendable_count=unsendable_count,
    )


async def publish_teacher_broadcast(
    session: AsyncSession,
    teacher: User,
    request: TeacherBroadcastPublishRequest,
    operation_key: uuid.UUID,
    *,
    now: datetime | None = None,
) -> TeacherBroadcastPublication:
    _require_teacher(teacher)
    teacher_id = teacher.id
    timestamp = normalize_utc(now or datetime.now(UTC))

    existing = await _get_by_operation_key(session, teacher_id, operation_key)
    if existing is not None:
        return _reconcile_existing(existing, request)

    students = await auth_service.get_teacher_student_roster(session, teacher_id)
    preview = _decode_confirmation_token(request.preview_token)
    jti = preview.get("jti")
    if (
        preview.get("purpose") != "teacher_broadcast_preview"
        or preview.get("teacher_id") != str(teacher_id)
        or preview.get("message_hash") != _message_hash(request.text)
        or preview.get("roster_hash") != _roster_hash(students)
        or not isinstance(jti, str)
        or len(jti) != 32
    ):
        raise Conflict("broadcast_preview_stale")

    eligible_count = sum(
        student.telegram_private_chat_id is not None for student in students
    )
    broadcast = TeacherBroadcast(
        id=uuid.uuid4(),
        teacher_id=teacher_id,
        operation_key=operation_key,
        confirmation_token=jti,
        message=request.text,
        recipient_count=len(students),
        eligible_count=eligible_count,
        unsendable_count=len(students) - eligible_count,
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(broadcast)
    session.add_all(
        NotificationIntent(
            id=uuid.uuid4(),
            recipient_id=student.id,
            notification_type=NotificationType.TEACHER_BROADCAST,
            dedupe_key=f"teacher-broadcast:{teacher_id}:{operation_key}:{student.id}",
            source_id=broadcast.id,
            scheduled_at=timestamp,
            available_at=timestamp,
            expires_at=timestamp + PUSH_CATCH_UP,
            state=NotificationState.PENDING,
            attempt_count=0,
            created_at=timestamp,
            updated_at=timestamp,
        )
        for student in students
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _get_by_operation_key(session, teacher_id, operation_key)
        if existing is not None:
            return _reconcile_existing(existing, request)
        used_confirmation = await session.scalar(
            select(TeacherBroadcast.id).where(
                TeacherBroadcast.confirmation_token == jti
            )
        )
        if used_confirmation is not None:
            raise Conflict("broadcast_confirmation_already_used")
        raise
    return _publication(broadcast)


async def reconcile_teacher_broadcast(
    session: AsyncSession,
    teacher: User,
    operation_key: uuid.UUID,
) -> TeacherBroadcastPublication:
    _require_teacher(teacher)
    broadcast = await _get_by_operation_key(session, teacher.id, operation_key)
    if broadcast is None:
        raise NotFound("broadcast_not_found")
    return _publication(broadcast)


async def get_teacher_broadcast_results(
    session: AsyncSession,
    teacher: User,
    operation_key: uuid.UUID,
    *,
    skip: int,
    limit: int,
) -> TeacherBroadcastResults:
    _require_teacher(teacher)
    broadcast = await _get_by_operation_key(session, teacher.id, operation_key)
    if broadcast is None:
        raise NotFound("broadcast_not_found")

    intent_filter = (
        NotificationIntent.notification_type == NotificationType.TEACHER_BROADCAST,
        NotificationIntent.source_id == broadcast.id,
    )
    grouped_counts = await session.execute(
        select(
            NotificationIntent.state,
            NotificationIntent.last_outcome,
            func.count(),
        )
        .where(*intent_filter)
        .group_by(NotificationIntent.state, NotificationIntent.last_outcome)
    )
    counts = {
        "pending": 0,
        "retrying": 0,
        "sent": 0,
        "suppressed": 0,
        "failed": 0,
        "unknown": 0,
    }
    for state, last_outcome, count in grouped_counts:
        counts[_delivery_status(state, last_outcome)] += count

    rows = await session.execute(
        select(
            NotificationIntent.recipient_id,
            User.full_name,
            NotificationIntent.state,
            NotificationIntent.last_outcome,
        )
        .join(User, User.id == NotificationIntent.recipient_id)
        .where(*intent_filter)
        .order_by(User.full_name.asc().nulls_last(), User.id)
        .offset(skip)
        .limit(limit)
    )
    recipients = [
        TeacherBroadcastRecipientResult(
            student_id=student_id,
            student_name=student_name,
            status=_delivery_status(state, last_outcome),
        )
        for student_id, student_name, state, last_outcome in rows
    ]
    return TeacherBroadcastResults(
        operation_key=broadcast.operation_key,
        total=sum(counts.values()),
        skip=skip,
        limit=limit,
        counts=TeacherBroadcastResultCounts(**counts),
        recipients=recipients,
    )


def _delivery_status(state: str, last_outcome: str | None) -> BroadcastDeliveryStatus:
    notification_state = NotificationState(state)
    if notification_state in {NotificationState.PENDING, NotificationState.CLAIMED}:
        return "pending"
    if notification_state == NotificationState.RETRY:
        return (
            "unknown"
            if last_outcome == AcknowledgementOutcome.UNKNOWN.name
            else "retrying"
        )
    if notification_state == NotificationState.SENT:
        return "sent"
    if notification_state == NotificationState.SUPPRESSED:
        return "suppressed"
    if notification_state == NotificationState.TERMINAL:
        return (
            "unknown"
            if last_outcome == AcknowledgementOutcome.UNKNOWN.name
            else "failed"
        )
    raise RuntimeError(f"Unsupported notification state: {notification_state}")


def _require_teacher(teacher: User) -> None:
    if teacher.role != Roles.TEACHER:
        raise Forbidden("teacher_broadcasts_require_teacher")


def _confirmation_token(
    teacher_id: uuid.UUID,
    text: str,
    students: list[User],
) -> str:
    return jwt.encode(
        {
            "teacher_id": str(teacher_id),
            "purpose": "teacher_broadcast_preview",
            "message_hash": _message_hash(text),
            "roster_hash": _roster_hash(students),
            "jti": uuid.uuid4().hex,
        },
        auth_settings.JWT_SECRET,
        algorithm=auth_settings.JWT_ALG,
    )


def _message_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _roster_hash(students: list[User]) -> str:
    roster = [
        [str(student.id), student.telegram_private_chat_id is not None]
        for student in sorted(students, key=lambda student: str(student.id))
    ]
    payload = json.dumps(roster, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _decode_confirmation_token(token: str) -> dict[str, object]:
    try:
        return jwt.decode(
            token,
            auth_settings.JWT_SECRET,
            algorithms=[auth_settings.JWT_ALG],
        )
    except InvalidTokenError as exc:
        raise Conflict("broadcast_preview_invalid") from exc


async def _get_by_operation_key(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    operation_key: uuid.UUID,
) -> TeacherBroadcast | None:
    results = await session.scalars(
        select(TeacherBroadcast).where(
            TeacherBroadcast.teacher_id == teacher_id,
            TeacherBroadcast.operation_key == operation_key,
        )
    )
    return results.one_or_none()


def _reconcile_existing(
    broadcast: TeacherBroadcast,
    request: TeacherBroadcastPublishRequest,
) -> TeacherBroadcastPublication:
    if broadcast.message != request.text:
        raise Conflict("broadcast_operation_key_reused")
    preview = _decode_confirmation_token(request.preview_token)
    if (
        preview.get("purpose") != "teacher_broadcast_preview"
        or preview.get("teacher_id") != str(broadcast.teacher_id)
        or preview.get("message_hash") != _message_hash(request.text)
        or preview.get("jti") != broadcast.confirmation_token
    ):
        raise Conflict("broadcast_operation_key_reused")
    return _publication(broadcast)


def _publication(broadcast: TeacherBroadcast) -> TeacherBroadcastPublication:
    return TeacherBroadcastPublication(
        id=broadcast.id,
        operation_key=broadcast.operation_key,
        recipient_count=broadcast.recipient_count,
        eligible_count=broadcast.eligible_count,
        unsendable_count=broadcast.unsendable_count,
    )

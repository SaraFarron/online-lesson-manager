import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.activity.constants import (
    SCHEDULE_ACTION_TYPES,
    ScheduleMutation,
)
from backend.activity.models import StudentAction
from backend.activity.schemas import StudentActionHistoryItem
from backend.auth.constants import Roles
from backend.auth.models import StudentTeacherRelationship, User
from backend.constants import EventType
from backend.events.models import Event, RecurrentEvent
from backend.homework.models import Homework, HomeworkSubmission


async def record_schedule_action(
    session: AsyncSession,
    actor: User,
    *,
    event_type: EventType,
    mutation: ScheduleMutation,
    resource_id: uuid.UUID,
    recurrent: bool = False,
    occurrence_start: datetime | None = None,
) -> StudentAction | None:
    if actor.role != Roles.STUDENT:
        return None
    action_type = SCHEDULE_ACTION_TYPES.get((event_type, mutation, recurrent))
    if action_type is None:
        return None

    if (
        mutation
        in {
            ScheduleMutation.CANCEL_OCCURRENCE,
            ScheduleMutation.MOVE_OCCURRENCE,
        }
        and occurrence_start is None
    ):
        raise ValueError("Occurrence actions require an occurrence start")
    if event_type == EventType.LESSON:
        resource_type = (
            "lesson_occurrence"
            if occurrence_start is not None
            else "lesson_series"
            if recurrent
            else "lesson"
        )
    else:
        resource_type = "vacation"
    return await record_student_action(
        session,
        actor,
        action_type,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_occurrence_at=occurrence_start,
    )


async def record_student_action(
    session: AsyncSession,
    student: User,
    action_type: str,
    *,
    relationship: StudentTeacherRelationship | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    resource_occurrence_at: datetime | None = None,
    occurred_at: datetime | None = None,
) -> StudentAction | None:
    if student.role != Roles.STUDENT:
        raise ValueError("Student actions require a student actor")
    if student.teacher_id is None:
        return None

    active_relationship = relationship
    if active_relationship is None:
        active_relationship = await session.scalar(
            select(StudentTeacherRelationship).where(
                StudentTeacherRelationship.student_id == student.id,
                StudentTeacherRelationship.teacher_id == student.teacher_id,
                StudentTeacherRelationship.removed_at.is_(None),
            )
        )
    if (
        active_relationship is None
        or active_relationship.student_id != student.id
        or active_relationship.teacher_id != student.teacher_id
        or active_relationship.removed_at is not None
    ):
        raise RuntimeError("Student has no matching active teacher relationship")

    action = StudentAction(
        relationship_id=active_relationship.id,
        teacher_id=active_relationship.teacher_id,
        student_id=student.id,
        actor_id=student.id,
        action_type=action_type,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_occurrence_at=resource_occurrence_at,
        occurred_at=occurred_at or datetime.now(UTC),
    )
    session.add(action)
    await session.flush()
    return action


async def get_recent_student_actions(
    session: AsyncSession,
    relationship_id: uuid.UUID,
    teacher_id: uuid.UUID,
    student_id: uuid.UUID,
) -> list[StudentActionHistoryItem]:
    result = await session.scalars(
        select(StudentAction)
        .where(
            StudentAction.relationship_id == relationship_id,
            StudentAction.teacher_id == teacher_id,
            StudentAction.student_id == student_id,
        )
        .order_by(StudentAction.occurred_at.desc(), StudentAction.id.desc())
        .limit(10)
    )
    actions = list(result)
    available_resources = await _available_resources(
        session, teacher_id, student_id, actions
    )

    return [
        StudentActionHistoryItem(
            id=action.id,
            action_type=action.action_type,
            occurred_at=action.occurred_at,
            resource_type=action.resource_type,
            resource_id=action.resource_id,
            resource_occurrence_at=action.resource_occurrence_at,
            resource_available=(
                None
                if action.resource_id is None and action.resource_type is None
                else (
                    action.resource_type is not None
                    and action.resource_id is not None
                    and (action.resource_type, action.resource_id)
                    in available_resources
                )
            ),
        )
        for action in actions
    ]


async def _available_resources(
    session: AsyncSession,
    teacher_id: uuid.UUID,
    student_id: uuid.UUID,
    actions: list[StudentAction],
) -> set[tuple[str, uuid.UUID]]:
    references: dict[str, set[uuid.UUID]] = {}
    for action in actions:
        if action.resource_type is not None and action.resource_id is not None:
            references.setdefault(action.resource_type, set()).add(action.resource_id)

    available: set[tuple[str, uuid.UUID]] = set()
    event_ids = references.get("lesson", set()) | references.get("vacation", set())
    if event_ids:
        events = await session.execute(
            select(Event.id, Event.event_type).where(
                Event.id.in_(event_ids),
                Event.teacher_id == teacher_id,
                Event.user_id == student_id,
            )
        )
        for event_id, event_type in events:
            resource_type = (
                "lesson"
                if event_type == EventType.LESSON
                else "vacation"
                if event_type == EventType.VACATION
                else None
            )
            if resource_type is not None and event_id in references.get(
                resource_type, set()
            ):
                available.add((resource_type, event_id))

    recurrent_ids = (
        references.get("lesson_series", set())
        | references.get("lesson_occurrence", set())
        | references.get("vacation", set())
    )
    if recurrent_ids:
        recurrent_events = await session.execute(
            select(RecurrentEvent.id, RecurrentEvent.event_type).where(
                RecurrentEvent.id.in_(recurrent_ids),
                RecurrentEvent.teacher_id == teacher_id,
                RecurrentEvent.user_id == student_id,
            )
        )
        for recurrent_id, event_type in recurrent_events:
            resource_type = (
                "lesson_series"
                if event_type == EventType.LESSON
                and recurrent_id in references.get("lesson_series", set())
                else "lesson_occurrence"
                if event_type == EventType.LESSON
                and recurrent_id in references.get("lesson_occurrence", set())
                else "vacation"
                if event_type == EventType.VACATION
                and recurrent_id in references.get("vacation", set())
                else None
            )
            if resource_type is not None:
                available.add((resource_type, recurrent_id))

    homework_ids = references.get("homework", set())
    if homework_ids:
        homework_result = await session.scalars(
            select(Homework.id).where(
                Homework.id.in_(homework_ids),
                Homework.teacher_id == teacher_id,
                Homework.student_id == student_id,
                Homework.deleted_at.is_(None),
                Homework.cleanup_pending.is_(False),
            )
        )
        available.update(("homework", homework_id) for homework_id in homework_result)

    submission_ids = references.get("homework_submission", set())
    if submission_ids:
        submission_result = await session.scalars(
            select(HomeworkSubmission.id)
            .join(Homework, Homework.id == HomeworkSubmission.homework_id)
            .where(
                HomeworkSubmission.id.in_(submission_ids),
                HomeworkSubmission.student_id == student_id,
                Homework.teacher_id == teacher_id,
                Homework.student_id == student_id,
                Homework.deleted_at.is_(None),
                Homework.cleanup_pending.is_(False),
            )
        )
        available.update(
            ("homework_submission", submission_id)
            for submission_id in submission_result
        )

    return available

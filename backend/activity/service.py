import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.activity.models import StudentAction
from backend.auth.constants import Roles
from backend.auth.models import StudentTeacherRelationship, User


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

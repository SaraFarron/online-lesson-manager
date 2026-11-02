import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models import Base


class StudentAction(Base):
    __tablename__ = "student_action"
    __table_args__ = (
        CheckConstraint(
            "actor_id = student_id",
            name="student_is_actor",
        ),
        ForeignKeyConstraint(
            ["relationship_id", "teacher_id", "student_id"],
            [
                "student_teacher_relationship.id",
                "student_teacher_relationship.teacher_id",
                "student_teacher_relationship.student_id",
            ],
            name="student_action_relationship_scope_fkey",
            ondelete="RESTRICT",
        ),
        Index(
            "student_action_relationship_occurred_id_idx",
            "relationship_id",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    relationship_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    teacher_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    actor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    resource_occurrence_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

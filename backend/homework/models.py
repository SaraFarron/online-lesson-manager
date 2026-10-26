import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.auth.models import User


class Homework(Base, TimestampMixin):
    __tablename__ = "homework"
    __table_args__ = (
        CheckConstraint(
            "text IS NULL OR length(trim(text)) > 0",
            name="text_nonempty",
        ),
        CheckConstraint(
            "(lesson_kind IS NULL AND lesson_source_id IS NULL "
            "AND lesson_occurrence_start IS NULL) OR "
            "(lesson_kind IN ('EVENT', 'RECURRENT_EVENT') "
            "AND lesson_source_id IS NOT NULL "
            "AND lesson_occurrence_start IS NOT NULL)",
            name="lesson_occurrence_consistent",
        ),
        CheckConstraint(
            "(status = 'INCOMPLETE' AND completed_at IS NULL "
            "AND completion_source IS NULL) OR "
            "(status = 'COMPLETE' AND completed_at IS NOT NULL "
            "AND completion_source IN ('STUDENT_SUBMISSION', 'TEACHER'))",
            name="completion_consistent",
        ),
        CheckConstraint(
            "status IN ('INCOMPLETE', 'COMPLETE')",
            name="status_valid",
        ),
        CheckConstraint(
            "NOT cleanup_pending OR deleted_at IS NOT NULL",
            name="cleanup_requires_deletion",
        ),
        UniqueConstraint(
            "teacher_id",
            "operation_key",
            name="homework_teacher_id_operation_key_key",
        ),
        Index("homework_teacher_created_idx", "teacher_id", "created_at", "id"),
        Index("homework_student_created_idx", "student_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    lesson_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lesson_source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    lesson_occurrence_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="INCOMPLETE", server_default="INCOMPLETE"
    )
    completion_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    operation_key: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cleanup_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    teacher: Mapped[User] = relationship(
        "User", foreign_keys=[teacher_id], viewonly=True
    )
    student: Mapped[User] = relationship(
        "User", foreign_keys=[student_id], viewonly=True
    )
    submissions: Mapped[list[HomeworkSubmission]] = relationship(
        back_populates="homework", cascade="save-update, merge"
    )


class HomeworkSubmission(Base, TimestampMixin):
    __tablename__ = "homework_submission"
    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "text IS NULL OR length(trim(text)) > 0",
            name="text_nonempty",
        ),
        UniqueConstraint(
            "homework_id",
            "version",
            name="homework_submission_homework_id_version_key",
        ),
        UniqueConstraint(
            "homework_id",
            "operation_key",
            name="homework_submission_homework_id_operation_key_key",
        ),
        Index("homework_submission_student_created_idx", "student_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    homework_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("homework.id", ondelete="RESTRICT"),
        nullable=False,
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_key: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    homework: Mapped[Homework] = relationship(back_populates="submissions")
    student: Mapped[User] = relationship(
        "User", foreign_keys=[student_id], viewonly=True
    )


class HomeworkAttachment(Base, TimestampMixin):
    __tablename__ = "homework_attachment"
    __table_args__ = (
        CheckConstraint(
            "byte_size >= 0 AND byte_size <= 20971520",
            name="byte_size_within_limit",
        ),
        CheckConstraint(
            "length(file_name) <= 128",
            name="file_name_within_limit",
        ),
        CheckConstraint(
            "(homework_id IS NULL AND submission_id IS NULL "
            "AND staged_until IS NOT NULL) OR "
            "(homework_id IS NOT NULL AND staged_until IS NULL)",
            name="staging_target_consistent",
        ),
        CheckConstraint(
            "submission_id IS NULL OR homework_id IS NOT NULL",
            name="submission_requires_homework",
        ),
        UniqueConstraint("storage_key", name="homework_attachment_storage_key_key"),
        Index("homework_attachment_homework_idx", "homework_id"),
        Index("homework_attachment_submission_idx", "submission_id"),
        Index("homework_attachment_owner_staged_idx", "owner_user_id", "staged_until"),
        Index("homework_attachment_cleanup_idx", "cleanup_pending"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    homework_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("homework.id", ondelete="RESTRICT"),
        nullable=True,
    )
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("homework_submission.id", ondelete="RESTRICT"),
        nullable=True,
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    file_name: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    staged_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cleanup_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

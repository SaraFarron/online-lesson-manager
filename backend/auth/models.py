import uuid
from datetime import UTC, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.events.models import Event, RecurrentEvent


class User(Base, TimestampMixin):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str | None] = mapped_column(String(256), unique=True, index=True)
    hashed_password: Mapped[str | None] = mapped_column(String(1024))
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    telegram_private_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    full_name: Mapped[str | None] = mapped_column(
        String(256), nullable=True, default=None
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String)
    teacher_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id"), nullable=True, default=None
    )
    teacher: Mapped[User | None] = relationship(
        "User", remote_side=[id], foreign_keys=[teacher_id], backref="students"
    )
    timezone: Mapped[str] = mapped_column(String)
    notification_lessons_today: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    notification_schedule_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.current_timestamp(),
    )
    student_profile: Mapped[StudentProfile | None] = relationship(
        "StudentProfile",
        back_populates="student",
        foreign_keys="[StudentProfile.student_id]",
    )
    teacher_profile: Mapped[TeacherProfile | None] = relationship(
        "TeacherProfile",
        back_populates="teacher",
        foreign_keys="[TeacherProfile.teacher_id]",
    )
    created_events: Mapped[list[Event]] = relationship(
        "Event", back_populates="user", foreign_keys="[Event.user_id]"
    )
    taught_events: Mapped[list[Event]] = relationship(
        "Event", back_populates="teacher", foreign_keys="[Event.teacher_id]"
    )
    created_recurrent_events: Mapped[list[RecurrentEvent]] = relationship(
        "RecurrentEvent", back_populates="user", foreign_keys="[RecurrentEvent.user_id]"
    )
    taught_recurrent_events: Mapped[list[RecurrentEvent]] = relationship(
        "RecurrentEvent",
        back_populates="teacher",
        foreign_keys="[RecurrentEvent.teacher_id]",
    )


class StudentProfile(Base, TimestampMixin):
    __tablename__ = "student_profile"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id"), unique=True, nullable=False
    )
    student: Mapped[User] = relationship(
        "User", back_populates="student_profile", foreign_keys=[student_id]
    )
    notification_lesson: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    notification_lesson_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.current_timestamp(),
    )
    notification_homework: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    notification_homework_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.current_timestamp(),
    )
    notification_homework_assignment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class TeacherProfile(Base, TimestampMixin):
    __tablename__ = "teacher_profile"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id"), unique=True, nullable=False
    )
    teacher: Mapped[User] = relationship(
        "User", back_populates="teacher_profile", foreign_keys=[teacher_id]
    )
    code: Mapped[str] = mapped_column(String, unique=True)
    work_start: Mapped[time | None] = mapped_column(Time, default=time(9))
    work_end: Mapped[time | None] = mapped_column(Time, default=time(18))
    working_days: Mapped[list[int]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: [1, 2, 3, 4, 5],
        server_default="[1,2,3,4,5]",
    )
    max_consecutive_lessons: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, server_default="4"
    )
    lesson_length: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    between_lessons_break: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5
    )
    max_lessons_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    notification_homework_submission: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class StudentTeacherRelationship(Base, TimestampMixin):
    __tablename__ = "student_teacher_relationship"
    __table_args__ = (
        CheckConstraint(
            "removed_at IS NULL OR linked_at IS NULL OR removed_at >= linked_at",
            name="removed_after_link",
        ),
        UniqueConstraint(
            "id",
            "teacher_id",
            "student_id",
            name="relationship_scope",
        ),
        Index(
            "student_teacher_relationship_one_active_per_student_idx",
            "student_id",
            unique=True,
            sqlite_where=text("removed_at IS NULL"),
            postgresql_where=text("removed_at IS NULL"),
        ),
        Index(
            "student_teacher_relationship_teacher_removed_student_idx",
            "teacher_id",
            "removed_at",
            "student_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    linked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class StudentRemovalOperation(Base, TimestampMixin):
    __tablename__ = "student_removal_operation"
    __table_args__ = (
        ForeignKeyConstraint(
            ["relationship_id", "teacher_id", "student_id"],
            [
                "student_teacher_relationship.id",
                "student_teacher_relationship.teacher_id",
                "student_teacher_relationship.student_id",
            ],
            name="student_removal_operation_relationship_scope_fkey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "teacher_id",
            "operation_key",
            name="student_removal_operation_teacher_operation_key_key",
        ),
        UniqueConstraint(
            "confirmation_token",
            name="student_removal_operation_confirmation_token_key",
        ),
        Index(
            "ix_student_removal_operation_teacher_id_student_id_created_at",
            "teacher_id",
            "student_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    relationship_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    teacher_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operation_key: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    confirmation_token: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    staged_attachment_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    removed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

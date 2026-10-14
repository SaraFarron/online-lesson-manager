import uuid
from datetime import time
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Time,
    Uuid,
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
    notification_homework: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
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

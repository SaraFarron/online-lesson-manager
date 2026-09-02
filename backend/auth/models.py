import uuid
from datetime import time
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, Time, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.events.models import Event, RecurrentEvent


class User(Base, TimestampMixin):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(1024))
    full_name: Mapped[str | None] = mapped_column(
        String(256), nullable=True, default=None
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String)
    teacher_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("user.id"), nullable=True, default=None)
    teacher: Mapped[User | None] = relationship("User", remote_side=[id], foreign_keys=[teacher_id], backref="students")
    timezone: Mapped[str] = mapped_column(String)
    notification_lessons_today: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    student_profile: Mapped[StudentProfile | None] = relationship(
        "StudentProfile", back_populates="student", foreign_keys="[StudentProfile.student_id]",
    )
    teacher_profile: Mapped[TeacherProfile | None] = relationship(
        "TeacherProfile", back_populates="teacher", foreign_keys="[TeacherProfile.teacher_id]",
    )
    events: Mapped[list[Event]] = relationship("Event", back_populates="user")
    recurrent_events: Mapped[list[RecurrentEvent]] = relationship("RecurrentEvent", back_populates="user")


class StudentProfile(Base, TimestampMixin):
    __tablename__ = "student_profile"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("user.id"), unique=True, nullable=False)
    student: Mapped[User] = relationship("User", back_populates="student_profile", foreign_keys=[student_id])
    notification_lesson: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    notification_homework: Mapped[int] = mapped_column(Integer, nullable=True, default=None)


class TeacherProfile(Base, TimestampMixin):
    __tablename__ = "teacher_profile"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("user.id"), unique=True, nullable=False)
    teacher: Mapped[User] = relationship("User", back_populates="teacher_profile", foreign_keys=[teacher_id])
    code: Mapped[str] = mapped_column(String)
    work_start: Mapped[time] = mapped_column(Time, nullable=True)
    work_end: Mapped[time] = mapped_column(Time, nullable=True)
    # weekends are stored as weekly events
    lesson_length: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    between_lessons_break: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_lessons_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=6)

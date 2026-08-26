import uuid
from datetime import time

from sqlalchemy import Boolean, ForeignKey, Integer, String, Time, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from backend.models import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(1024))
    full_name: Mapped[str | None] = mapped_column(
        String(256), nullable=True, default=None
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String)
    teacher_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=True, default=None)
    timezone: Mapped[str] = mapped_column(String)
    notification_lessons_today: Mapped[int] = mapped_column(Integer, nullable=True, default=None)


class StudentProfile(Base, TimestampMixin):
    __tablename__ = "student_profile"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), unique=True, nullable=False)
    notification_lesson: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    notification_homework: Mapped[int] = mapped_column(Integer, nullable=True, default=None)


class TeacherProfile(Base, TimestampMixin):
    __tablename__ = "teacher_profile"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String)
    work_start: Mapped[time] = mapped_column(Time, nullable=True)
    work_end: Mapped[time] = mapped_column(Time, nullable=True)
    # weekends are stored as weekly events
    lesson_length: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    between_lessons_break: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_lessons_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=6)

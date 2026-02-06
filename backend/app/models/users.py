from datetime import datetime, time
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models import Event, RecurrentEvent


class UserRoles(str, Enum):
    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"


class User(Base):
    """User model."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(unique=True, nullable=True)
    teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    username: Mapped[str | None] = mapped_column(String(150), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(50), default="student")
    is_active: Mapped[bool] = mapped_column(default=True)
    # UserToken field
    token: Mapped[str | None] = relationship(
        "UserToken",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    invite_code: Mapped[str | None] = mapped_column(String(25), unique=True, nullable=True)

    # Self-referential relationship: user's teacher
    teacher: Mapped["User | None"] = relationship(
        "User",
        remote_side=[id],
        back_populates="students",
    )

    # Students of this user (if they are a teacher)
    students: Mapped[list["User"]] = relationship(
        "User",
        back_populates="teacher",
    )

    # One-to-one relationship with TeacherSettings
    teacher_settings: Mapped["TeacherSettings | None"] = relationship(
        "TeacherSettings",
        back_populates="teacher",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # Events where this user is the teacher
    events_as_teacher: Mapped[list["Event"]] = relationship(
        "Event",
        foreign_keys="[Event.teacher_id]",
        back_populates="teacher",
    )

    # Events where this user is the student
    events_as_student: Mapped[list["Event"]] = relationship(
        "Event",
        foreign_keys="[Event.student_id]",
        back_populates="student",
    )

    # Recurrent events where this user is the teacher
    recurrent_events_as_teacher: Mapped[list["RecurrentEvent"]] = relationship(
        "RecurrentEvent",
        foreign_keys="[RecurrentEvent.teacher_id]",
        back_populates="teacher",
    )

    # Recurrent events where this user is the student
    recurrent_events_as_student: Mapped[list["RecurrentEvent"]] = relationship(
        "RecurrentEvent",
        foreign_keys="[RecurrentEvent.student_id]",
        back_populates="student",
    )

    # User action history
    history: Mapped[list["UserHistory"]] = relationship(
        "UserHistory",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    # User settings
    settings: Mapped["UserSettings"] = relationship(
        "UserSettings",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    class Roles:
        STUDENT: str = "student"
        TEACHER: str = "teacher"
        ADMIN: str = "admin"

    def working_hours(self) -> tuple[time, time]:
        """Return the working hours of the teacher."""
        if self.role == User.Roles.TEACHER and self.teacher_settings:
            start = self.teacher_settings.work_start
            end = self.teacher_settings.work_end
        elif self.role == self.Roles.STUDENT and self.teacher and self.teacher.teacher_settings:
            start = self.teacher.teacher_settings.work_start
            end = self.teacher.teacher_settings.work_end
        else:
            start = time(9, 0)
            end = time(17, 0)
        return start, end


class UserToken(Base):
    """User token model."""

    __tablename__ = "user_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationship back to User
    user: Mapped["User"] = relationship("User")


class TeacherSettings(Base):
    """Teacher settings model."""

    __tablename__ = "teacher_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,  # Ensures one-to-one relationship
        nullable=False,
    )
    work_start: Mapped[time] = mapped_column(Time, nullable=False)  # e.g., time(9, 0)
    work_end: Mapped[time] = mapped_column(Time, nullable=False)  # e.g., time(17, 0)

    # One-to-one relationship back to User
    teacher: Mapped["User"] = relationship(
        "User",
        back_populates="teacher_settings",
    )


class UserSettings(Base):
    """User settings model."""

    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    morning_notification: Mapped[time] = mapped_column(Time, nullable=True)
    user: Mapped["User"] = relationship("User", back_populates="settings")


class UserHistory(Base):
    """User history model."""

    __tablename__ = "user_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    action_value: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationship back to User
    user: Mapped["User"] = relationship("User", back_populates="history")

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.events.constants import RecurrenceMode
from backend.models import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.auth.models import User


class Event(Base, TimestampMixin):
    __tablename__ = "event"
    __table_args__ = (
        CheckConstraint(
            "post_break_minutes >= 0", name="post_break_minutes_nonnegative"
        ),
        CheckConstraint(
            "event_type = 'LESSON' OR post_break_minutes = 0",
            name="post_break_minutes_lessons_only",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id"), nullable=False
    )
    teacher: Mapped[User] = relationship(
        "User", back_populates="taught_events", foreign_keys=[teacher_id]
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id"), nullable=False
    )
    user: Mapped[User] = relationship(
        "User", back_populates="created_events", foreign_keys=[user_id]
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    post_break_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class RecurrentEvent(Base, TimestampMixin):
    __tablename__ = "recurrent_event"
    __table_args__ = (
        CheckConstraint("interval > 0", name="interval_positive"),
        CheckConstraint('"end" > start', name="start_before_end"),
        CheckConstraint(
            "(interval_end IS NOT NULL AND interval_end > start) OR "
            "(interval_end IS NULL AND event_type = 'LESSON' AND \"interval\" = 7)",
            name="interval_end_after_start",
        ),
        CheckConstraint(
            "recurrence_mode IN ('UTC_STEP', 'TEACHER_LOCAL')",
            name="recurrence_mode_valid",
        ),
        CheckConstraint(
            "(recurrence_mode = 'UTC_STEP' AND recurrence_timezone IS NULL) OR "
            "(recurrence_mode = 'TEACHER_LOCAL' AND recurrence_timezone IS NOT NULL "
            "AND length(trim(recurrence_timezone)) > 0)",
            name="recurrence_timezone_consistent",
        ),
        CheckConstraint(
            "post_break_minutes >= 0", name="post_break_minutes_nonnegative"
        ),
        CheckConstraint(
            "event_type = 'LESSON' OR post_break_minutes = 0",
            name="post_break_minutes_lessons_only",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id"), nullable=False
    )
    teacher: Mapped[User] = relationship(
        "User", back_populates="taught_recurrent_events", foreign_keys=[teacher_id]
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id"), nullable=False
    )
    user: Mapped[User] = relationship(
        "User", back_populates="created_recurrent_events", foreign_keys=[user_id]
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    interval: Mapped[int] = mapped_column(Integer, nullable=False)  # in days
    interval_end: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # when the recurrence ends
    recurrence_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=RecurrenceMode.UTC_STEP.value,
        server_default=RecurrenceMode.UTC_STEP.value,
    )
    recurrence_timezone: Mapped[str | None] = mapped_column(String, nullable=True)
    post_break_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class RecurrentCancels(Base, TimestampMixin):
    __tablename__ = "recurrent_cancels"
    __table_args__ = (
        UniqueConstraint(
            "recurrent_event_id",
            "start",
            name="recurrent_cancels_recurrent_event_id_start_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    recurrent_event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recurrent_event.id"), nullable=False
    )
    start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TeacherScheduleGuard(Base):
    __tablename__ = "teacher_schedule_guard"

    teacher_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
    )

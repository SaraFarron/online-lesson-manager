from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Sequence, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models import User

# Custom sequences for unique IDs across events and recurrent_events
# Events get odd IDs (1, 3, 5, ...)
# RecurrentEvents get even IDs (2, 4, 6, ...)
events_id_seq = Sequence("events_id_seq", start=1, increment=2)
recurrent_events_id_seq = Sequence("recurrent_events_id_seq", start=2, increment=2)


class Event(Base):
    """Event model."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(
        events_id_seq,
        primary_key=True,
        server_default=events_id_seq.next_value(),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_reschedule: Mapped[bool] = mapped_column(default=False)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationship to User (teacher)
    teacher: Mapped["User"] = relationship(
        "User",
        foreign_keys=[teacher_id],
        back_populates="events_as_teacher",
    )

    # Relationship to User (student)
    student: Mapped["User"] = relationship(
        "User",
        foreign_keys=[student_id],
        back_populates="events_as_student",
    )

    class Types:
        LESSON = "lesson"


class RecurrentEvent(Base):
    """Recurrent Event model."""

    __tablename__ = "recurrent_events"

    id: Mapped[int] = mapped_column(
        recurrent_events_id_seq,
        primary_key=True,
        server_default=recurrent_events_id_seq.next_value(),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interval_days: Mapped[int] = mapped_column(nullable=False)  # e.g., 7 for weekly
    interval_end: Mapped[datetime | None] = mapped_column(nullable=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relationship to User (teacher)
    teacher: Mapped["User"] = relationship(
        "User",
        foreign_keys=[teacher_id],
        back_populates="recurrent_events_as_teacher",
    )

    # Relationship to User (student)
    student: Mapped["User"] = relationship(
        "User",
        foreign_keys=[student_id],
        back_populates="recurrent_events_as_student",
    )

    # Cancellations for this recurrent event
    cancellations: Mapped[list["RecurrentCancels"]] = relationship(
        "RecurrentCancels",
        back_populates="recurrent_event",
        cascade="all, delete-orphan",
    )


class RecurrentCancels(Base):
    """Recurrent Event Cancellations model."""

    __tablename__ = "recurrent_cancels"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    recurrent_event_id: Mapped[int] = mapped_column(
        ForeignKey("recurrent_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    canceled_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Relationship to RecurrentEvent
    recurrent_event: Mapped["RecurrentEvent"] = relationship(
        "RecurrentEvent",
        back_populates="cancellations",
    )

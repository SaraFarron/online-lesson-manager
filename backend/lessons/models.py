import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.auth.models import User

class Event(Base, TimestampMixin):
    __tablename__ = "event"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("user.id"), nullable=False)
    user: Mapped[User] = relationship("User", back_populates="events", foreign_keys=[user_id])
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RecurrentEvent(Base, TimestampMixin):
    __tablename__ = "recurrent_event"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("user.id"), nullable=False)
    user: Mapped[User] = relationship("User", back_populates="recurrent_events", foreign_keys=[user_id])
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    interval: Mapped[int] = mapped_column(Integer, nullable=False)  # in days
    interval_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # when the recurrence ends


class RecurrentCancels(Base, TimestampMixin):
    __tablename__ = "recurrent_cancels"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recurrent_event_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("recurrent_event.id"), nullable=False)
    start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end: Mapped[datetime] = mapped_column(DateTime, nullable=False)

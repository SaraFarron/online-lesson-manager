import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models import Base, TimestampMixin


class Notification(Base, TimestampMixin):
    __tablename__ = "notification"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    receivers: Mapped[str] = mapped_column(String(255), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(String(1024), nullable=False)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)


class NotificationIntent(Base, TimestampMixin):
    __tablename__ = "notification_intent"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="notification_intent_dedupe_key_key"),
        CheckConstraint(
            "notification_type IN "
            "('DAILY_DIGEST', 'LESSON_REMINDER', 'HOMEWORK_REMINDER', "
            "'ASSIGNMENT_CREATED', 'SUBMISSION_RECEIVED', 'TEACHER_BROADCAST')",
            name="notification_type_valid",
        ),
        CheckConstraint(
            "state IN ('PENDING', 'CLAIMED', 'RETRY', 'SENT', 'TERMINAL', 'SUPPRESSED')",
            name="state_valid",
        ),
        CheckConstraint(
            "last_outcome IS NULL OR last_outcome IN "
            "('SENT', 'RETRYABLE', 'TERMINAL', 'UNKNOWN')",
            name="last_outcome_valid",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "(state = 'CLAIMED' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(state != 'CLAIMED' AND lease_token IS NULL "
            "AND lease_expires_at IS NULL)",
            name="lease_state_consistent",
        ),
        CheckConstraint("expires_at > scheduled_at", name="expiry_after_schedule"),
        Index("ix_notification_intent_state_available_at", "state", "available_at"),
        Index(
            "ix_notification_intent_state_lease_expires_at",
            "state",
            "lease_expires_at",
        ),
        Index(
            "ix_notification_intent_recipient_id_created_at",
            "recipient_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    notification_type: Mapped[str] = mapped_column(String(32), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_version: Mapped[int | None] = mapped_column(Integer)
    source_date: Mapped[date | None] = mapped_column(Date)
    occurrence_source_type: Mapped[str | None] = mapped_column(String(24))
    occurrence_source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    occurrence_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="PENDING", server_default="PENDING"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_outcome: Mapped[str | None] = mapped_column(String(16))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TeacherBroadcast(Base, TimestampMixin):
    __tablename__ = "teacher_broadcast"
    __table_args__ = (
        UniqueConstraint(
            "teacher_id",
            "operation_key",
            name="teacher_broadcast_teacher_operation_key_key",
        ),
        UniqueConstraint(
            "confirmation_token",
            name="teacher_broadcast_confirmation_token_key",
        ),
        CheckConstraint(
            "recipient_count >= 0 AND eligible_count >= 0 "
            "AND unsendable_count >= 0 "
            "AND recipient_count = eligible_count + unsendable_count",
            name="recipient_counts_consistent",
        ),
        Index("ix_teacher_broadcast_teacher_id_created_at", "teacher_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    operation_key: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    confirmation_token: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unsendable_count: Mapped[int] = mapped_column(Integer, nullable=False)

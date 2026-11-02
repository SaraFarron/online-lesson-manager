from datetime import timedelta
from enum import StrEnum


class NotificationType(StrEnum):
    DAILY_DIGEST = "DAILY_DIGEST"
    LESSON_REMINDER = "LESSON_REMINDER"
    HOMEWORK_REMINDER = "HOMEWORK_REMINDER"
    ASSIGNMENT_CREATED = "ASSIGNMENT_CREATED"
    SUBMISSION_RECEIVED = "SUBMISSION_RECEIVED"
    TEACHER_BROADCAST = "TEACHER_BROADCAST"


class NotificationState(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RETRY = "RETRY"
    SENT = "SENT"
    TERMINAL = "TERMINAL"
    SUPPRESSED = "SUPPRESSED"


class AcknowledgementOutcome(StrEnum):
    SENT = "sent"
    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    UNKNOWN = "unknown"


MAX_DELIVERY_ATTEMPTS = 8
MAX_CLAIM_BATCH = 50
CLAIM_LEASE_SECONDS = 60
PUSH_CATCH_UP = timedelta(hours=24)

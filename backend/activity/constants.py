from enum import StrEnum

from backend.constants import EventType


class StudentActionType(StrEnum):
    ACCOUNT_REGISTERED = "account_registered"
    TEACHER_LINKED = "teacher_linked"
    ACCOUNT_PROFILE_UPDATED = "account_profile_updated"
    STUDENT_PROFILE_CREATED = "student_profile_created"
    STUDENT_SETTINGS_UPDATED = "student_settings_updated"
    LESSON_CREATED = "lesson_created"
    LESSON_UPDATED = "lesson_updated"
    LESSON_DELETED = "lesson_deleted"
    LESSON_SERIES_CREATED = "lesson_series_created"
    LESSON_SERIES_UPDATED = "lesson_series_updated"
    LESSON_SERIES_DELETED = "lesson_series_deleted"
    LESSON_OCCURRENCE_CANCELLED = "lesson_occurrence_cancelled"
    LESSON_OCCURRENCE_MOVED = "lesson_occurrence_moved"
    VACATION_CREATED = "vacation_created"
    VACATION_UPDATED = "vacation_updated"
    VACATION_DELETED = "vacation_deleted"
    HOMEWORK_SUBMISSION_CREATED = "homework_submission_created"
    HOMEWORK_COMPLETED = "homework_completed"


class ScheduleMutation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    CANCEL_OCCURRENCE = "cancel_occurrence"
    MOVE_OCCURRENCE = "move_occurrence"


SCHEDULE_ACTION_TYPES = {
    (
        EventType.LESSON,
        ScheduleMutation.CREATE,
        False,
    ): StudentActionType.LESSON_CREATED,
    (
        EventType.LESSON,
        ScheduleMutation.UPDATE,
        False,
    ): StudentActionType.LESSON_UPDATED,
    (
        EventType.LESSON,
        ScheduleMutation.DELETE,
        False,
    ): StudentActionType.LESSON_DELETED,
    (
        EventType.LESSON,
        ScheduleMutation.CREATE,
        True,
    ): StudentActionType.LESSON_SERIES_CREATED,
    (
        EventType.LESSON,
        ScheduleMutation.UPDATE,
        True,
    ): StudentActionType.LESSON_SERIES_UPDATED,
    (
        EventType.LESSON,
        ScheduleMutation.DELETE,
        True,
    ): StudentActionType.LESSON_SERIES_DELETED,
    (
        EventType.LESSON,
        ScheduleMutation.CANCEL_OCCURRENCE,
        True,
    ): StudentActionType.LESSON_OCCURRENCE_CANCELLED,
    (
        EventType.LESSON,
        ScheduleMutation.MOVE_OCCURRENCE,
        True,
    ): StudentActionType.LESSON_OCCURRENCE_MOVED,
    (
        EventType.VACATION,
        ScheduleMutation.CREATE,
        False,
    ): StudentActionType.VACATION_CREATED,
    (
        EventType.VACATION,
        ScheduleMutation.UPDATE,
        False,
    ): StudentActionType.VACATION_UPDATED,
    (
        EventType.VACATION,
        ScheduleMutation.DELETE,
        False,
    ): StudentActionType.VACATION_DELETED,
}

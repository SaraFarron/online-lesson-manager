from enum import StrEnum


class RecurrenceMode(StrEnum):
    UTC_STEP = "UTC_STEP"
    TEACHER_LOCAL = "TEACHER_LOCAL"


AUTOMATIC_BREAK_READ_ONLY_DETAIL = (
    "Automatic breaks are derived and cannot be edited or deleted independently; "
    "manage the parent lesson instead"
)

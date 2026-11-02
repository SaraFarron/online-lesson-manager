from enum import StrEnum


class StudentActionType(StrEnum):
    ACCOUNT_REGISTERED = "account_registered"
    TEACHER_LINKED = "teacher_linked"
    ACCOUNT_PROFILE_UPDATED = "account_profile_updated"
    STUDENT_PROFILE_CREATED = "student_profile_created"
    STUDENT_SETTINGS_UPDATED = "student_settings_updated"

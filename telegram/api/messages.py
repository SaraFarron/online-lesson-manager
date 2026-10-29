from typing import Literal

ErrorContext = Literal["default", "schedule", "scheduling", "settings", "homework"]


DEFAULT_ERROR_MESSAGES = {
    400: "Account inactive or request not allowed.",
    401: "Authentication failed. Check bot configuration or restart with /start.",
    403: "Access denied.",
    404: "Account or invitation not found.",
    409: "Account already registered.",
    422: "Invalid settings. Review the values before submitting again.",
}


SCHEDULE_ERROR_MESSAGES = {
    400: "This account cannot view a schedule.",
    401: "Schedule authentication failed. Please retry or restart with /start.",
    403: "This account does not have schedule access.",
    404: "The linked teacher is not available for schedule viewing.",
    409: "The linked teacher cannot provide a schedule.",
    422: "The schedule request or calendar data is invalid.",
}


SCHEDULING_ERROR_MESSAGES = {
    400: "This scheduling action is not allowed for this account.",
    401: "Authentication failed. Restart with /start and reload the event.",
    403: "This account does not have permission to manage this event.",
    404: "The selected student or event is no longer available.",
    409: "The request conflicts with the current schedule. Reload and try again.",
    422: "Check the date, time, and selected student.",
}


SETTINGS_ERROR_MESSAGES = {
    400: "This account cannot update these settings.",
    401: "Authentication failed. Reload settings or restart with /start.",
    403: "This account cannot update the selected profile.",
    404: "The account or profile is no longer available.",
    409: "Settings changed or conflict with saved values. Reload and review them.",
    422: "Invalid settings. Review the values and merged limits before saving.",
}


HOMEWORK_ERROR_MESSAGES = {
    400: "This homework action is not allowed for this account.",
    401: "Homework authentication failed. Restart with /start and reload your homework.",
    403: "This account does not have permission to access this homework.",
    404: "This homework or attachment is no longer available. Reload the homework list.",
    409: "Homework changed or was already completed. Reload it before trying again.",
    413: "This attachment exceeds the allowed 20 MiB file limit.",
    422: "Check the homework content, attachments, and file limits.",
}


POLICY_ERROR_MESSAGES = {
    "Teacher work hours are not configured": "The teacher has not configured working hours.",
    "Teacher working days are not configured": "The teacher has not configured working days.",
    "Lesson starts on a non-working day": "That date is not one of the teacher's working days.",
    "Lesson starts before the teacher's work hours": "That time is before the teacher's working hours.",
    "Lesson ends after the teacher's work hours": "That time is after the teacher's working hours.",
    "Lesson cannot cross local midnight": "A lesson cannot cross local midnight in the teacher's timezone.",
    "Students must use the teacher's configured lesson duration": "Use the lesson duration configured by the teacher.",
    "Default lesson duration is outside the supported range": "The teacher's configured lesson duration is outside the supported range.",
    "Event overlaps an applicable vacation": "That time falls within an applicable vacation.",
    "Event overlaps an automatic post-lesson break": "That time overlaps an automatic break reserved after a lesson. Choose another time.",
    "Automatic post-lesson break overlaps another scheduled event": "The automatic break after this lesson would conflict with another scheduled event. Choose a different time or duration.",
    "Event overlaps an existing non-lesson event": "That time overlaps another scheduled event.",
    "Event overlaps an existing event": "That time overlaps another scheduled event.",
}

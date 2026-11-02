from telegram.api.models.notifications import ClaimedNotification

_MESSAGES = {
    "DAILY_DIGEST": "Your lesson digest is ready. Send /start to view your schedule.",
    "LESSON_REMINDER": "You have an upcoming lesson. Send /start to view your schedule.",
    "HOMEWORK_REMINDER": "A homework deadline is approaching. Send /start to view homework.",
    "ASSIGNMENT_CREATED": "You have new homework. Send /start to view the assignment.",
    "SUBMISSION_RECEIVED": "A student submitted homework. Send /start to review it.",
}


def render_notification(notification: ClaimedNotification) -> str:
    return _MESSAGES[notification.notification_type]

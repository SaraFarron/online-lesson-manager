from html import escape
from unicodedata import category

from telegram.api.models.notifications import ClaimedNotification

MAX_TELEGRAM_TEXT_UNITS = 4096
MAX_PAGE_BODY_UNITS = 3900

_TITLES = {
    "DAILY_DIGEST": "Today's lesson schedule",
    "LESSON_REMINDER": "Lesson reminder",
    "HOMEWORK_REMINDER": "Incomplete homework reminder",
    "ASSIGNMENT_CREATED": "New homework assignment",
    "SUBMISSION_RECEIVED": "Homework solution received",
    "TEACHER_BROADCAST": "Message from your teacher",
}


def render_notification(notification: ClaimedNotification) -> list[str]:
    title = _TITLES[notification.notification_type]
    body = "\n".join(_telegram_safe_text(line) for line in notification.content)
    if not body:
        body = "Open /start in Telegram to view the related details."
    chunks = _split_utf16(body, MAX_PAGE_BODY_UNITS)
    total = len(chunks)
    pages: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        page_label = f"\n<i>Part {index} of {total}</i>" if total > 1 else ""
        rendered = (
            f"<b>{escape(title, quote=False)}</b>\n\n"
            f"{escape(chunk, quote=False)}{page_label}"
        )
        visible = title + "\n\n" + chunk
        if total > 1:
            visible += f"\nPart {index} of {total}"
        if _utf16_length(visible) > MAX_TELEGRAM_TEXT_UNITS:
            raise RuntimeError("Rendered notification exceeds Telegram's text limit")
        pages.append(rendered)
    return pages


def _split_utf16(value: str, limit: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    units = 0
    for index, character in enumerate(value):
        character_units = 2 if ord(character) > 0xFFFF else 1
        if units + character_units > limit:
            chunks.append(value[start:index])
            start = index
            units = 0
        units += character_units
    chunks.append(value[start:])
    return chunks


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _telegram_safe_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        character
        for character in normalized
        if character in "\n\t" or category(character) not in {"Cc", "Cs"}
    )

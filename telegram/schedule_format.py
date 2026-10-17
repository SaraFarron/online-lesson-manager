from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from typing import Literal
from unicodedata import category
from zoneinfo import ZoneInfo

from telegram.api import ScheduleDay, ScheduleItem, ScheduleResponse

ViewerRole = Literal["TEACHER", "STUDENT"]
MAX_MESSAGE_UTF16_UNITS = 3500
MAX_LABEL_CODEPOINTS = 80
PAGE_INDICATOR_RESERVE_UNITS = 32

_EVENT_LABELS = {
    "LESSON": "Lesson",
    "BREAK": "Break",
    "VACATION": "Vacation",
}


@dataclass(frozen=True, slots=True)
class _TextLine:
    markup: str
    plain: str


def format_schedule_pages(
    response: ScheduleResponse, role: ViewerRole
) -> tuple[str, ...]:
    """Format a typed backend snapshot into complete, Telegram-safe HTML pages."""
    zone = ZoneInfo(response.timezone)
    current_date = response.generated_at.astimezone(zone).date()
    updated_at = response.generated_at.astimezone(zone)
    title = "Today's schedule" if response.period == "day" else "This week's schedule"
    header_lines = (
        _TextLine(f"<b>{title}</b>", title),
        _TextLine(
            f"Timezone: {escape(response.timezone)}",
            f"Timezone: {response.timezone}",
        ),
        _TextLine(
            f"Updated at {updated_at:%Y-%m-%d %H:%M} ({escape(response.timezone)})",
            f"Updated at {updated_at:%Y-%m-%d %H:%M} ({response.timezone})",
        ),
    )
    header_markup = "\n".join(line.markup for line in header_lines)
    header_plain = "\n".join(line.plain for line in header_lines)
    body_limit = (
        MAX_MESSAGE_UTF16_UNITS
        - _utf16_length(header_plain)
        - PAGE_INDICATOR_RESERVE_UNITS
        - 2
    )
    if body_limit <= 0:
        raise ValueError("Schedule header exceeds the Telegram message budget")

    chunks: list[tuple[_TextLine, ...]] = []
    for day in response.days:
        chunks.extend(_day_chunks(day, role, zone, current_date, body_limit))
    if not chunks:
        raise ValueError("Schedule response must include at least one day")

    pages = _pack_chunks(chunks, body_limit)
    total_pages = len(pages)
    return tuple(
        _render_page(header_markup, header_plain, page, index, total_pages)
        for index, page in enumerate(pages, start=1)
    )


def _day_chunks(
    day: ScheduleDay,
    role: ViewerRole,
    zone: ZoneInfo,
    current_date: date,
    body_limit: int,
) -> list[tuple[_TextLine, ...]]:
    heading = day.date.strftime("%A, %B %d, %Y")
    heading_line = _TextLine(f"<b>{heading}</b>", heading)
    is_current_day = day.date == current_date

    if not day.items:
        content = [_TextLine("No events", "No events")]
        if is_current_day:
            content.append(_now_divider())
    elif is_current_day:
        ended = [item for item in day.items if item.status == "past"]
        not_ended = [item for item in day.items if item.status != "past"]
        content = [_render_item(item, role, zone) for item in ended]
        content.append(_now_divider())
        content.extend(_render_item(item, role, zone) for item in not_ended)
    else:
        content = [_render_item(item, role, zone) for item in day.items]

    chunks: list[tuple[_TextLine, ...]] = []
    current = [heading_line]
    for line in content:
        candidate = (*current, line)
        if _lines_utf16_length(candidate) <= body_limit:
            current.append(line)
            continue
        if len(current) == 1:
            raise ValueError("A schedule row exceeds the Telegram message budget")
        chunks.append(tuple(current))
        current = [heading_line, line]
        if _lines_utf16_length(current) > body_limit:
            raise ValueError("A schedule row exceeds the Telegram message budget")
    if len(current) > 1 or not chunks:
        chunks.append(tuple(current))
    return chunks


def _render_item(item: ScheduleItem, role: ViewerRole, zone: ZoneInfo) -> _TextLine:
    start = item.start.astimezone(zone)
    end = item.end.astimezone(zone)
    interval = f"{_format_time(start, zone)}–{_format_time(end, zone)}"
    days_later = (end.date() - start.date()).days
    if days_later > 0:
        unit = "day" if days_later == 1 else "days"
        interval += f" (+{days_later} {unit})"

    event_label = _EVENT_LABELS[item.event_type]
    subject = ""
    markup_subject = ""
    if role == "TEACHER" and item.event_type == "LESSON":
        if item.subject_label is None:
            raise ValueError("Teacher lesson response is missing its subject label")
        safe_label = _safe_label(item.subject_label)
        subject = f" — {safe_label}"
        markup_subject = f" — {escape(safe_label, quote=False)}"

    status = item.status.upper()
    plain = f"{interval}  {event_label}{subject}  [{status}]"
    markup = (
        f"{escape(interval, quote=False)}  {event_label}{markup_subject}  [{status}]"
    )
    return _TextLine(markup, plain)


def _now_divider() -> _TextLine:
    return _TextLine("── <b>NOW</b> ──", "── NOW ──")


def _format_time(value: datetime, zone: ZoneInfo) -> str:
    local = value.astimezone(zone)
    clock = local.strftime("%H:%M")
    naive = local.replace(tzinfo=None)
    early_offset = naive.replace(tzinfo=zone, fold=0).utcoffset()
    late_offset = naive.replace(tzinfo=zone, fold=1).utcoffset()
    if early_offset == late_offset:
        return clock
    abbreviation = local.tzname() or "local"
    return f"{clock} {abbreviation} (UTC{_format_offset(local.utcoffset())})"


def _format_offset(offset: timedelta | None) -> str:
    if offset is None:
        return "+00:00"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _safe_label(value: str) -> str:
    single_line = "".join(
        " " if category(character) in {"Cc", "Cs"} else character for character in value
    )
    normalized = " ".join(single_line.split())
    if len(normalized) <= MAX_LABEL_CODEPOINTS:
        return normalized
    return f"{normalized[: MAX_LABEL_CODEPOINTS - 1]}…"


def _pack_chunks(
    chunks: list[tuple[_TextLine, ...]], body_limit: int
) -> list[tuple[tuple[_TextLine, ...], ...]]:
    pages: list[tuple[tuple[_TextLine, ...], ...]] = []
    current: list[tuple[_TextLine, ...]] = []
    for chunk in chunks:
        candidate = (*current, chunk)
        if _chunks_utf16_length(candidate) <= body_limit:
            current.append(chunk)
            continue
        if not current:
            raise ValueError("A schedule date exceeds the Telegram message budget")
        pages.append(tuple(current))
        current = [chunk]
    if current:
        pages.append(tuple(current))
    return pages


def _render_page(
    header_markup: str,
    header_plain: str,
    chunks: tuple[tuple[_TextLine, ...], ...],
    page_number: int,
    total_pages: int,
) -> str:
    body_markup = "\n\n".join(_lines_markup(chunk) for chunk in chunks)
    body_plain = "\n\n".join(_lines_plain(chunk) for chunk in chunks)
    footer = f"Page {page_number}/{total_pages}"
    text = f"{header_markup}\n{body_markup}\n{footer}"
    visible_text = f"{header_plain}\n{body_plain}\n{footer}"
    if _utf16_length(visible_text) > MAX_MESSAGE_UTF16_UNITS:
        raise ValueError("Formatted schedule exceeds the Telegram message budget")
    return text


def _chunks_utf16_length(chunks: tuple[tuple[_TextLine, ...], ...]) -> int:
    return _utf16_length("\n\n".join(_lines_plain(chunk) for chunk in chunks))


def _lines_utf16_length(lines: Sequence[_TextLine]) -> int:
    return _utf16_length(_lines_plain(lines))


def _lines_markup(lines: tuple[_TextLine, ...]) -> str:
    return "\n".join(line.markup for line in lines)


def _lines_plain(lines: Sequence[_TextLine]) -> str:
    return "\n".join(line.plain for line in lines)


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2

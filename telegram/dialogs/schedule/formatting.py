from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Literal
from unicodedata import category
from zoneinfo import ZoneInfo

from telegram.api import ScheduleDay, ScheduleItem, ScheduleResponse
from telegram.shared.inputs import format_compact_schedule_date, format_human_time

ViewerRole = Literal["TEACHER", "STUDENT"]
MAX_MESSAGE_UTF16_UNITS = 3500
MAX_LABEL_CODEPOINTS = 80

_EVENT_LABELS = {
    "LESSON": "Lesson",
    "BREAK": "Break",
    "VACATION": "Vacation",
}
_AUTOMATIC_BREAK_LABELS: dict[ViewerRole, str] = {
    "TEACHER": "Automatic break",
    "STUDENT": "Teacher's automatic break",
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
            f"Updated {escape(format_compact_schedule_date(current_date, reference_date=current_date))} "
            f"at {escape(format_human_time(updated_at))}",
            f"Updated {format_compact_schedule_date(current_date, reference_date=current_date)} "
            f"at {format_human_time(updated_at)}",
        ),
    )
    header_markup = "\n".join(line.markup for line in header_lines)
    header_plain = "\n".join(line.plain for line in header_lines)
    empty_today = response.period == "day" and not any(
        day.items for day in response.days
    )
    timezone_footer = (
        None
        if empty_today
        else _TextLine(
            f"Timezone: {escape(response.timezone)}",
            f"Timezone: {response.timezone}",
        )
    )
    footer_length = _utf16_length(timezone_footer.plain) if timezone_footer else 0
    footer_separator = 1 if timezone_footer else 0
    body_limit = (
        MAX_MESSAGE_UTF16_UNITS
        - _utf16_length(header_plain)
        - 1
        - footer_separator
        - footer_length
    )
    if body_limit <= 0:
        raise ValueError("Schedule header exceeds the Telegram message budget")

    chunks: list[tuple[_TextLine, ...]] = []
    for day in response.days:
        chunks.extend(
            _day_chunks(
                day,
                role,
                zone,
                current_date,
                body_limit,
                suppress_empty_today_now=empty_today,
            )
        )
    if not chunks:
        raise ValueError("Schedule response must include at least one day")

    pages = _pack_chunks(chunks, body_limit)
    return tuple(
        _render_page(header_markup, header_plain, page, timezone_footer)
        for page in pages
    )


def _day_chunks(
    day: ScheduleDay,
    role: ViewerRole,
    zone: ZoneInfo,
    current_date: date,
    body_limit: int,
    *,
    suppress_empty_today_now: bool,
) -> list[tuple[_TextLine, ...]]:
    heading = format_compact_schedule_date(day.date, reference_date=current_date)
    heading_line = _TextLine(f"<b>{heading}</b>", heading)
    is_current_day = day.date == current_date

    if not day.items:
        content = [_TextLine("No events", "No events")]
        if is_current_day and not suppress_empty_today_now:
            content.append(_now_divider())
    elif is_current_day:
        ended = [item for item in day.items if item.status == "past"]
        not_ended = [item for item in day.items if item.status != "past"]
        content = [_render_item(item, role, zone, current_date) for item in ended]
        content.append(_now_divider())
        content.extend(
            _render_item(item, role, zone, current_date) for item in not_ended
        )
    else:
        content = [_render_item(item, role, zone, current_date) for item in day.items]

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


def _render_item(
    item: ScheduleItem,
    role: ViewerRole,
    zone: ZoneInfo,
    reference_date: date,
) -> _TextLine:
    start = item.start.astimezone(zone)
    end = item.end.astimezone(zone)
    start_clock = format_human_time(start)
    end_clock = format_human_time(end)
    if start.date() == end.date():
        interval = f"{start_clock}–{end_clock}"
    else:
        start_date = format_compact_schedule_date(
            start.date(), reference_date=reference_date
        )
        end_date = format_compact_schedule_date(
            end.date(), reference_date=reference_date
        )
        interval = f"{start_date} {start_clock}–{end_date} {end_clock}"

    event_label = (
        _AUTOMATIC_BREAK_LABELS[role]
        if item.source_kind == "automatic_break"
        else _EVENT_LABELS[item.event_type]
    )
    subject = ""
    markup_subject = ""
    if role == "TEACHER" and item.event_type == "LESSON":
        if item.subject_label is None:
            raise ValueError("Teacher lesson response is missing its subject label")
        safe_label = _safe_label(item.subject_label)
        subject = f" — {safe_label}"
        markup_subject = f" — {escape(safe_label, quote=False)}"

    status = "⏳" if item.status == "upcoming" else f"[{item.status.upper()}]"
    plain = f"{interval}  {event_label}{subject}  {status}"
    markup = f"{escape(interval, quote=False)}  {event_label}{markup_subject}  {status}"
    return _TextLine(markup, plain)


def _now_divider() -> _TextLine:
    return _TextLine("── <b>NOW</b> ──", "── NOW ──")


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
    footer: _TextLine | None,
) -> str:
    body_markup = "\n\n".join(_lines_markup(chunk) for chunk in chunks)
    body_plain = "\n\n".join(_lines_plain(chunk) for chunk in chunks)
    text = f"{header_markup}\n{body_markup}"
    visible_text = f"{header_plain}\n{body_plain}"
    if footer is not None:
        text += f"\n{footer.markup}"
        visible_text += f"\n{footer.plain}"
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

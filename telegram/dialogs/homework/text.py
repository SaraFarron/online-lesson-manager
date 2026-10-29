from dataclasses import dataclass
from html import escape
from typing import Any

from aiogram_dialog import DialogManager, ShowMode

MAX_HOMEWORK_TEXT_PAGE_UNITS = 1300


@dataclass(frozen=True)
class HomeworkTextPage:
    heading: str
    content: str
    has_previous: bool
    has_next: bool


def homework_text_page(
    text: str | None,
    page_index: int,
    *,
    label: str,
    max_units: int = MAX_HOMEWORK_TEXT_PAGE_UNITS,
) -> HomeworkTextPage:
    source = text or "(no text)"
    if max_units < 2:
        raise ValueError("Text page size must fit a UTF-16 surrogate pair")
    pages = _split_utf16(source, max_units)
    index = min(max(page_index, 0), len(pages) - 1)
    heading = (
        f"{label} (page {index + 1} of {len(pages)}):\n"
        if len(pages) > 1
        else f"{label}: "
    )
    return HomeworkTextPage(
        heading=heading,
        content=escape(pages[index], quote=True),
        has_previous=index > 0,
        has_next=index + 1 < len(pages),
    )


def _split_utf16(text: str, max_units: int) -> list[str]:
    pages: list[str] = []
    current: list[str] = []
    current_units = 0
    for character in text:
        units = 2 if ord(character) > 0xFFFF else 1
        if current and current_units + units > max_units:
            pages.append("".join(current))
            current = []
            current_units = 0
        current.append(character)
        current_units += units
    pages.append("".join(current))
    return pages


async def change_homework_text_page(
    manager: DialogManager, key: str, offset: int
) -> None:
    data: dict[str, Any] = manager.dialog_data
    data[key] = max(0, int(data.get(key, 0)) + offset)
    await manager.show(show_mode=ShowMode.EDIT)

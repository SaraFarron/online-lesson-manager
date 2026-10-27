from html import unescape

import pytest

from telegram.homework_text import (
    MAX_HOMEWORK_TEXT_PAGE_UNITS,
    homework_text_page,
)


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def test_homework_text_pages_preserve_escaped_unicode_without_splitting_emoji() -> None:
    source = "<assignment> & 'quoted' 🚀 " * 240
    pages = []
    index = 0
    while True:
        page = homework_text_page(source, index, label="Assignment text")
        pages.append(page)
        if not page.has_next:
            break
        index += 1

    assert len(pages) > 1
    assert pages[0].heading == f"Assignment text (page 1 of {len(pages)}):\n"
    assert not pages[0].has_previous
    assert not pages[-1].has_next
    assert all(
        _utf16_units(unescape(page.content)) <= MAX_HOMEWORK_TEXT_PAGE_UNITS
        for page in pages
    )
    assert "".join(unescape(page.content) for page in pages) == source
    assert all("<assignment>" not in page.content for page in pages)


def test_short_homework_text_keeps_compact_layout_and_clamps_page_index() -> None:
    first = homework_text_page("Read chapter 3", 0, label="Text")
    overflow = homework_text_page("Read chapter 3", 4, label="Text")

    assert first.heading == "Text: "
    assert first.content == overflow.content == "Read chapter 3"
    assert not first.has_previous and not first.has_next


def test_homework_text_page_rejects_budget_smaller_than_one_emoji() -> None:
    with pytest.raises(ValueError, match="surrogate pair"):
        homework_text_page("🚀", 0, label="Text", max_units=1)

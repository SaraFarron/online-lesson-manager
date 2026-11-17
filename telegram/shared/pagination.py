def page_count(total: int, page_size: int) -> int:
    return max(1, (total + page_size - 1) // page_size)


def page_label(page_number: int, pages: int, summary: str = "") -> str:
    """Return the page indicator, or an empty string when everything fits on one page."""
    if pages <= 1:
        return ""
    label = f"Page {page_number} of {pages}"
    return f"{label} {summary}" if summary else label


def page_line(page_number: int, pages: int, summary: str = "") -> str:
    """Return the page indicator as its own line, or nothing for a single page."""
    label = page_label(page_number, pages, summary)
    return f"{label}\n" if label else ""

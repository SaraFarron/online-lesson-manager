from telegram.shared.pagination import page_count, page_label


def test_page_label_is_hidden_for_a_single_page() -> None:
    assert page_label(1, 1, "· 1 student(s)") == ""
    assert page_label(1, page_count(0, 20)) == ""


def test_page_label_is_shown_for_multiple_pages() -> None:
    assert page_label(2, page_count(21, 20), "(21 students)") == (
        "Page 2 of 2 (21 students)"
    )
    assert page_label(1, 3) == "Page 1 of 3"

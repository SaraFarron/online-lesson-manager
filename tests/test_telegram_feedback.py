from telegram.shared.feedback import feedback_block


def test_feedback_block_escapes_reason_and_keeps_multiline_text() -> None:
    error = """<unexpected> & "quoted" 'text'
Second line 😀"""

    assert feedback_block(error=error) == (
        "<b>❌ Error</b>\n"
        "&lt;unexpected&gt; &amp; &quot;quoted&quot; &#x27;text&#x27;\n"
        "Second line 😀\n\n"
    )


def test_feedback_block_renders_warning_and_combined_feedback_in_order() -> None:
    assert feedback_block(warning="The result is unknown.") == (
        "<b>⚠️ Attention</b>\nThe result is unknown.\n\n"
    )
    assert feedback_block(error="Rejected", warning="Result unknown") == (
        "<b>❌ Error</b>\nRejected\n\n<b>⚠️ Attention</b>\nResult unknown\n\n"
    )


def test_feedback_block_is_empty_without_feedback_and_stable_on_repeated_render() -> (
    None
):
    assert feedback_block() == ""
    assert feedback_block(error="") == ""

    first_render = feedback_block(error="<invalid>")
    second_render = feedback_block(error="<invalid>")

    assert first_render == second_render
    assert first_render.count("<b>") == 1
    assert first_render.count("&lt;invalid&gt;") == 1

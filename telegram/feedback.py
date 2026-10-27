from html import escape


def feedback_block(*, error: str | None = None, warning: str | None = None) -> str:
    blocks = []
    if error:
        blocks.append(f"<b>❌ Error</b>\n{escape(error, quote=True)}")
    if warning:
        blocks.append(f"<b>⚠️ Attention</b>\n{escape(warning, quote=True)}")
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n\n"

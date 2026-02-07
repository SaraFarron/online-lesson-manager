from math import ceil

from aiogram.utils.keyboard import InlineKeyboardBuilder


def inline_keyboard(buttons: dict[str, str], adjust: int | None = None):
    """Create an inline keyboard."""
    if not buttons:
        return None
    builder = InlineKeyboardBuilder()
    for callback_data, text in buttons.items():
        builder.button(text=text, callback_data=callback_data)
    if adjust is None:
        adjust = ceil(len(buttons) / 6)
    builder.adjust(adjust if adjust else 1, repeat=True)
    return builder.as_markup()

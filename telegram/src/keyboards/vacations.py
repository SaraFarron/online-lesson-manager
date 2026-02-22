from datetime import UTC, datetime

from src.keyboards.builder import inline_keyboard
from src.service.cache import Vacation


def vacations(data: list[Vacation], add_callback: str, remove_callback: str):
    buttons = {}
    now = datetime.now(UTC)
    for slot in data:
        if slot.end.year != now.year or slot.start.year != now.year or slot.start.year != slot.end.year:
            slot_str = f"{slot.start.strftime('%Y-%m-%d')} - {slot.end.strftime('%Y-%m-%d')}"
        else:
            slot_str = f"{slot.start.strftime('%m-%d')} - {slot.end.strftime('%m-%d')}"
        buttons[f"{remove_callback}/{slot_str}"] = f"Удалить каникулы {slot_str}"
    buttons[add_callback] = "Добавить каникулы"
    return inline_keyboard(buttons)

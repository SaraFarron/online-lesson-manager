"""Shared paginated time-slot grid for the lesson and break dialogs."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.types import CallbackQuery
from aiogram_dialog import DialogManager, ShowMode
from aiogram_dialog.widgets.kbd import Button, Group, Row, Select
from aiogram_dialog.widgets.kbd.base import Keyboard
from aiogram_dialog.widgets.text import Const, Format

from telegram.api import ResolvedLocalInstant

SLOT_PAGE_SIZE = 24
SLOT_COLUMNS = 4
DEFAULT_SLOT_STEP = 15
FINE_SLOT_STEP = 5


def slot_id(instant: ResolvedLocalInstant) -> str:
    return instant.utc.strftime("%Y%m%dT%H%M%S")


def _zoned_local(instant: ResolvedLocalInstant, timezone: str) -> datetime:
    return instant.utc.astimezone(ZoneInfo(timezone))


def slot_label(instant: ResolvedLocalInstant, timezone: str) -> str:
    local = _zoned_local(instant, timezone)
    label = local.strftime("%H:%M")
    first = local.replace(fold=0).utcoffset()
    if first != local.replace(fold=1).utcoffset():
        label += " (1st)" if local.fold == 0 else " (2nd)"
    return label


def reset_slot_step(data: dict[str, Any]) -> None:
    data["slot_step"] = DEFAULT_SLOT_STEP


def store_slots(
    data: dict[str, Any], instants: Sequence[ResolvedLocalInstant], timezone: str
) -> None:
    data.update(
        slot_options=[
            {
                "id": slot_id(instant),
                "label": slot_label(instant, timezone),
                "local": _zoned_local(instant, timezone)
                .replace(tzinfo=None)
                .isoformat(),
                "utc": instant.utc.isoformat(),
            }
            for instant in instants
        ],
        slot_skip=0,
    )


def clear_slots(data: dict[str, Any]) -> None:
    data.update(slot_options=[], slot_skip=0)


def find_slot(data: dict[str, Any], selected_id: str) -> dict[str, Any] | None:
    return next(
        (
            option
            for option in data.get("slot_options", [])
            if option["id"] == selected_id
        ),
        None,
    )


def slot_local_start(slot: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(slot["local"])


def slot_picker_data(data: dict[str, Any]) -> dict[str, Any]:
    options = data.get("slot_options", [])
    skip = int(data.get("slot_skip", 0))
    page = options[skip : skip + SLOT_PAGE_SIZE]
    step = int(data.get("slot_step", DEFAULT_SLOT_STEP))
    return {
        "slot_buttons": [(option["label"], option["id"]) for option in page],
        "has_slots": bool(options),
        "has_previous_slots": skip > 0,
        "has_next_slots": skip + len(page) < len(options),
        "slot_toggle_label": (
            "Show 5-minute times"
            if step == DEFAULT_SLOT_STEP
            else "Show 15-minute times"
        ),
    }


def toggle_slot_step(data: dict[str, Any]) -> int:
    step = (
        FINE_SLOT_STEP
        if int(data.get("slot_step", DEFAULT_SLOT_STEP)) == DEFAULT_SLOT_STEP
        else DEFAULT_SLOT_STEP
    )
    data["slot_step"] = step
    return step


async def _previous_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    data["slot_skip"] = max(0, int(data.get("slot_skip", 0)) - SLOT_PAGE_SIZE)
    await manager.show(show_mode=ShowMode.EDIT)


async def _next_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    data = manager.dialog_data
    data["slot_skip"] = int(data.get("slot_skip", 0)) + SLOT_PAGE_SIZE
    await manager.show(show_mode=ShowMode.EDIT)


def slot_picker_widgets(*, on_select: Any, on_toggle_step: Any) -> tuple[Keyboard, ...]:
    """Grid, page controls and the 15/5-minute toggle; ``slot_picker_data`` feeds them."""
    return (
        Group(
            Select(
                Format("{item[0]}"),
                id="slot",
                item_id_getter=lambda item: item[1],
                items="slot_buttons",
                on_click=on_select,
            ),
            width=SLOT_COLUMNS,
        ),
        Row(
            Button(
                Const("◀ Previous"),
                id="slot_previous",
                on_click=_previous_page,
                when="has_previous_slots",
            ),
            Button(
                Const("Next ▶"),
                id="slot_next",
                on_click=_next_page,
                when="has_next_slots",
            ),
        ),
        Button(
            Format("{slot_toggle_label}"),
            id="slot_step",
            on_click=on_toggle_step,
        ),
    )

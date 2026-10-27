from datetime import date, timedelta
from html import escape
from typing import Any, Literal

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
from aiogram_dialog import Dialog, DialogManager, ShowMode, StartMode, Window
from aiogram_dialog.widgets.kbd import Button, Row
from aiogram_dialog.widgets.text import Const, Format
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from telegram.api import BackendError, BackendUnavailable, SessionView
from telegram.onboarding import backend
from telegram.schedule_format import ViewerRole, format_schedule_pages

SchedulePeriod = Literal["day", "week"]


class ScheduleScreens(StatesGroup):
    view = State()


class ScheduleDialogState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: SchedulePeriod
    role: ViewerRole
    requested_date: date | None = None
    pages: list[str] = Field(default_factory=list)
    page: int = Field(default=0, ge=0)
    error: str = ""

    @model_validator(mode="after")
    def validate_state(self) -> ScheduleDialogState:
        if self.period == "day" and self.requested_date is not None:
            raise ValueError("Day schedule state cannot contain a requested date")
        if self.pages and self.page >= len(self.pages):
            raise ValueError("Schedule page is outside the available pages")
        if not self.pages and not self.error:
            raise ValueError("Schedule state must contain pages or an error")
        return self


async def open_today(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _open_schedule(callback, manager, "day")


async def open_week(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _open_schedule(callback, manager, "week")


async def open_schedule_week(
    callback: CallbackQuery, manager: DialogManager, requested_date: date
) -> None:
    await _open_schedule(callback, manager, "week", requested_date)


async def _open_schedule(
    callback: CallbackQuery,
    manager: DialogManager,
    period: SchedulePeriod,
    requested_date: date | None = None,
) -> None:
    view = SessionView.model_validate(manager.dialog_data["view"])
    if view.account.role not in {"TEACHER", "STUDENT"}:
        raise ValueError("This account cannot view a schedule")
    role: ViewerRole = view.account.role
    state = await _load_schedule(
        callback, manager, period, role, requested_date=requested_date
    )
    await manager.start(
        ScheduleScreens.view,
        data=state.model_dump(mode="json"),
        mode=StartMode.NORMAL,
        show_mode=ShowMode.EDIT,
    )


async def schedule_started(start_data: Any, manager: DialogManager) -> None:
    state = ScheduleDialogState.model_validate(start_data)
    manager.dialog_data.clear()
    manager.dialog_data.update(state.model_dump(mode="json"))


async def switch_to_today(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _refresh(callback, manager, "day")


async def switch_to_week(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _refresh(callback, manager, "week")


async def previous_week(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _navigate_week(callback, manager, -7)


async def next_week(
    callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await _navigate_week(callback, manager, 7)


async def _navigate_week(
    callback: CallbackQuery, manager: DialogManager, days: int
) -> None:
    state = _state(manager)
    if state.requested_date is None:
        return
    await _refresh(
        callback,
        manager,
        "week",
        state.requested_date + timedelta(days=days),
    )


async def _refresh(
    callback: CallbackQuery,
    manager: DialogManager,
    period: SchedulePeriod,
    requested_date: date | None = None,
) -> None:
    current = _state(manager)
    state = await _load_schedule(
        callback, manager, period, current.role, requested_date
    )
    manager.dialog_data.clear()
    manager.dialog_data.update(state.model_dump(mode="json"))
    await manager.show(show_mode=ShowMode.EDIT)


async def previous_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    state = _state(manager)
    if state.page == 0:
        return
    manager.dialog_data["page"] = state.page - 1
    await manager.show(show_mode=ShowMode.EDIT)


async def next_page(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    state = _state(manager)
    if state.page + 1 >= len(state.pages):
        return
    manager.dialog_data["page"] = state.page + 1
    await manager.show(show_mode=ShowMode.EDIT)


async def back_to_home(
    _callback: CallbackQuery, _button: Button, manager: DialogManager
) -> None:
    await manager.done(show_mode=ShowMode.EDIT)


async def schedule_data(dialog_manager: DialogManager, **_: Any) -> dict[str, Any]:
    state = _state(dialog_manager)
    if state.pages:
        schedule_text = state.pages[state.page]
    else:
        schedule_text = (
            f"{escape(state.error)}\n"
            "Use the available schedule-view buttons to switch views, "
            "or return home and reopen the schedule."
        )
    return {
        "schedule_text": schedule_text,
        "has_previous": state.page > 0,
        "has_next": state.page + 1 < len(state.pages),
        "show_today": state.period != "day",
        "show_week": state.period != "week",
        "show_previous_week": (
            state.period == "week" and state.requested_date is not None
        ),
        "show_next_week": state.period == "week" and state.requested_date is not None,
    }


def _state(manager: DialogManager) -> ScheduleDialogState:
    return ScheduleDialogState.model_validate(manager.dialog_data)


async def _load_schedule(
    callback: CallbackQuery,
    manager: DialogManager,
    period: SchedulePeriod,
    role: ViewerRole,
    requested_date: date | None = None,
) -> ScheduleDialogState:
    if callback.message is None:
        raise ValueError("Schedule viewing requires a private chat")
    client = backend(manager)
    try:
        response = (
            await client.read_schedule_day(
                callback.from_user.id, callback.message.chat.id
            )
            if period == "day"
            else await client.read_schedule_week(
                callback.from_user.id,
                callback.message.chat.id,
                requested_date=requested_date,
            )
        )
        pages = format_schedule_pages(response, role)
    except BackendError as exc:
        return ScheduleDialogState(
            period=period,
            role=role,
            requested_date=requested_date if period == "week" else None,
            error=str(exc),
        )
    except BackendUnavailable:
        return ScheduleDialogState(
            period=period,
            role=role,
            requested_date=requested_date if period == "week" else None,
            error="Schedule is unavailable right now.",
        )
    except ValidationError, ValueError:
        return ScheduleDialogState(
            period=period,
            role=role,
            requested_date=requested_date if period == "week" else None,
            error="The schedule response could not be displayed.",
        )
    return ScheduleDialogState(
        period=period,
        role=role,
        requested_date=response.days[0].date if period == "week" else None,
        pages=list(pages),
    )


def build_schedule_dialog() -> Dialog:
    return Dialog(
        Window(
            Format("{schedule_text}"),
            Row(
                Button(
                    Const("◀ Previous"),
                    id="previous",
                    on_click=previous_page,
                    when="has_previous",
                ),
                Button(
                    Const("Next ▶"),
                    id="next",
                    on_click=next_page,
                    when="has_next",
                ),
            ),
            Row(
                Button(
                    Const("◀ Previous week"),
                    id="previous_week",
                    on_click=previous_week,
                    when="show_previous_week",
                ),
                Button(
                    Const("Next week ▶"),
                    id="next_week",
                    on_click=next_week,
                    when="show_next_week",
                ),
            ),
            Row(
                Button(
                    Const("Today"),
                    id="today",
                    on_click=switch_to_today,
                    when="show_today",
                ),
                Button(
                    Const("This week"),
                    id="week",
                    on_click=switch_to_week,
                    when="show_week",
                ),
                Button(Const("Back to home"), id="home", on_click=back_to_home),
            ),
            state=ScheduleScreens.view,
        ),
        getter=schedule_data,
        on_start=schedule_started,
    )

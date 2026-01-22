from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas import EventResponse, TimeRangeResponse
from app.services import EventService

router = APIRouter()


@router.get("/day", response_model=list[EventResponse])
async def schedule_for_day(
    db: DatabaseSession,
    user: CurrentUser,
    day: Annotated[str, Query(regex=r"^\d{4}-\d{2}-\d{2}$")],
) -> list[EventResponse]:
    """Get schedule of events for a specific day."""
    service = EventService(db)
    day_date = datetime.strptime(day, "%Y-%m-%d").date()
    events = await service.get_schedule(user, day_date)
    return [EventResponse.from_models(event) for event in events]


@router.get("/range", response_model=dict[str, list[EventResponse]])
async def schedule_for_range(
    db: DatabaseSession,
    user: CurrentUser,
    start_day: Annotated[str, Query(regex=r"^\d{4}-\d{2}-\d{2}$")],
    end_day: Annotated[str, Query(regex=r"^\d{4}-\d{2}-\d{2}$")],
) -> dict[str, list[EventResponse]]:
    """Get schedule of events for a range of dates."""
    service = EventService(db)
    start_date = datetime.strptime(start_day, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_day, "%Y-%m-%d").date()
    schedule = await service.get_schedule_range(user, start_date, end_date)
    return {date: [EventResponse.from_models(event) for event in events] for date, events in schedule.items()}


@router.get("/free-slots/day", response_model=list[TimeRangeResponse])
async def get_free_slots(
    db: DatabaseSession,
    user: CurrentUser,
    day: Annotated[str, Query(regex=r"^\d{4}-\d{2}-\d{2}$")],
) -> list[TimeRangeResponse]:
    """Get free time slots for a specific day."""
    service = EventService(db)
    day_date = datetime.strptime(day, "%Y-%m-%d").date()
    free_slots = await service.get_free_slots(user, day_date)
    return [
        TimeRangeResponse(
            start=slot_start.isoformat(), end=slot_end.isoformat()
        ) for slot_start, slot_end in free_slots
    ]


@router.get("/free-slots/range", response_model=dict[str, list[TimeRangeResponse]])
async def get_free_slots_range(
    db: DatabaseSession,
    user: CurrentUser,
    start_day: Annotated[str, Query(regex=r"^\d{4}-\d{2}-\d{2}$")],
    end_day: Annotated[str, Query(regex=r"^\d{4}-\d{2}-\d{2}$")],
) -> dict[str, list[TimeRangeResponse]]:
    """Get free time slots for a range of dates."""
    service = EventService(db)
    start_date = datetime.strptime(start_day, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_day, "%Y-%m-%d").date()
    free_slots = await service.get_free_slots_range(user, start_date, end_date)
    return {
        date: [
            TimeRangeResponse(start=slot_start.isoformat(), end=slot_end.isoformat()) for slot_start, slot_end in slots
        ]
        for date, slots in free_slots.items()
    }


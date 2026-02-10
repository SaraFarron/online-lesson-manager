from datetime import date
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
    day: Annotated[date, Query()],
) -> list[EventResponse]:
    """Get schedule of events for a specific day."""
    service = EventService(db)
    events = await service.get_schedule(user, day)
    return [EventResponse.from_models(event) for event in events]


@router.get("/range", response_model=dict[str, list[EventResponse]])
async def schedule_for_range(
    db: DatabaseSession,
    user: CurrentUser,
    start_day: Annotated[date, Query()],
    end_day: Annotated[date, Query()],
) -> dict[str, list[EventResponse]]:
    """Get schedule of events for a range of dates."""
    service = EventService(db)
    schedule = await service.get_schedule_range(user, start_day, end_day)
    return {date: [EventResponse.from_models(event) for event in events] for date, events in schedule.items()}


@router.get("/free-slots/day", response_model=list[TimeRangeResponse])
async def get_free_slots(
    db: DatabaseSession,
    user: CurrentUser,
    day: Annotated[date, Query()],
) -> list[TimeRangeResponse]:
    """Get free time slots for a specific day."""
    service = EventService(db)
    free_slots = await service.get_free_slots(user, day)
    return [
        TimeRangeResponse(
            start=slot_start.isoformat(), end=slot_end.isoformat()
        ) for slot_start, slot_end in free_slots
    ]


@router.get("/free-slots/range", response_model=dict[str, list[TimeRangeResponse]])
async def get_free_slots_range(
    db: DatabaseSession,
    user: CurrentUser,
    start_day: Annotated[date, Query()],
    end_day: Annotated[date, Query()],
) -> dict[str, list[TimeRangeResponse]]:
    """Get free time slots for a range of dates."""
    service = EventService(db)
    free_slots = await service.get_free_slots_range(user, start_day, end_day)
    return {
        date: [
            TimeRangeResponse(start=slot["start"].isoformat(), end=slot["end"].isoformat()) for slot in slots
        ]
        for date, slots in free_slots.items()
    }


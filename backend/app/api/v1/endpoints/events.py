from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas import EventCreate, EventResponse, EventsTotalResponse
from app.services import EventService

router = APIRouter()


@router.get("/", response_model=EventsTotalResponse)
async def get_lessons_by_day(  # TODO: add recurrent events
    db: DatabaseSession,
    user: CurrentUser,
) -> EventsTotalResponse:
    """Get user's events."""
    service = EventService(db)
    events = await service.get_events_by_user(user)
    return EventsTotalResponse(
        total=len(events),
        events=[EventResponse.model_validate(event) for event in events],
    )


@router.get("/{event_id}", response_model=EventResponse)
async def get_event_by_id(  # TODO: add recurrent events
    db: DatabaseSession,
    user: CurrentUser,
    event_id: int,
) -> EventResponse:
    """Get event by ID."""
    service = EventService(db)
    event = await service.get_event_by_id(event_id, user)
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event with id {event_id} not found",
        )
    return EventResponse.model_validate(event)


@router.post("/", response_model=list[EventResponse])
async def create_event(
    db: DatabaseSession,
    user: CurrentUser,
    event: EventCreate,
):
    """Create a new event."""
    service = EventService(db)
    created_event = await service.create_event(event, user)
    return [EventResponse.model_validate(created_event)]

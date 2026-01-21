from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas import EventCreate, EventResponse, EventsTotalResponse
from app.services import EventService

router = APIRouter()


@router.get("/", response_model=EventsTotalResponse)
async def get_events(
    db: DatabaseSession,
    user: CurrentUser,
) -> EventsTotalResponse:
    """Get user's events."""
    service = EventService(db)
    events = await service.get_events_by_user(user)
    return EventsTotalResponse(
        total=len(events),
        events=[EventResponse.from_models(event) for event in events],
    )


@router.get("/{event_id}", response_model=EventResponse)
async def get_event_by_id(
    db: DatabaseSession,
    user: CurrentUser,
    event_id: int,
) -> EventResponse:
    """
    Get event by ID.
    
    - Odd IDs (1, 3, 5, ...) are regular events
    - Even IDs (2, 4, 6, ...) are recurrent events
    """
    service = EventService(db)
    
    if event_id % 2 == 1:  # Odd ID = regular event
        event = await service.get_event_by_id(event_id, user)
    else:  # Even ID = recurrent event
        event = await service.get_recurrent_event_by_id(event_id, user)
    
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event with id {event_id} not found",
        )
    return EventResponse.from_models(event)


@router.post("/", response_model=list[EventResponse])
async def create_event(
    db: DatabaseSession,
    user: CurrentUser,
    event: EventCreate,
):
    """Create a new event."""
    service = EventService(db)
    created_event = await service.create_event(event, user)
    return [EventResponse.from_models(created_event)]

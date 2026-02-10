from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas import EventCreate, EventResponse, EventsTotalResponse, EventUpdate
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
    """
    Create a new event.

    All datetime values must be in UTC timezone (ISO 8601 format with 'Z' suffix).
    Example: "2026-02-10T09:00:00Z"
    """
    service = EventService(db)
    try:
        created_event = await service.create_event(event, user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    return [EventResponse.from_models(created_event)]


@router.put("{event_id}", response_model=EventResponse)
async def update_event(
    db: DatabaseSession,
    user: CurrentUser,
    event: EventUpdate,
    event_id: int,
) -> EventResponse:
    """Update an existing event."""
    service = EventService(db)
    if event_id % 2 == 1:  # Odd ID = regular event
        updated_event = await service.update_event(event, user, event_id)
    else:  # Even ID = recurrent event
        updated_event = await service.update_recurrent_event(event, user, event_id)
    if updated_event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event with id {event_id} does not exist",
        )
    return EventResponse.from_models(updated_event)


@router.delete("{event_id}")
async def delete_event(
    db: DatabaseSession,
    user: CurrentUser,
    event_id: int,
):
    """Delete an existing event."""
    service = EventService(db)
    if event_id % 2 == 1:  # Odd ID = regular event
        success = await service.delete_event(event_id, user)
    else:  # Even ID = recurrent event
        success = await service.delete_recurrent_event(event_id, user)
    if success:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Event with id {event_id} does not exist or you don't own it"
    )

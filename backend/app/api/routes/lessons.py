import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, SessionDep
from app.models import (
    Event,
    EventCreate,
    Events,
    EventUpdate,
    Message,
    RecurrentEvent,
    RecurrentEventCreate,
    RecurrentEvents,
    RecurrentEventUpdate,
)

router = APIRouter(prefix="/lessons", tags=["lessons"])


@router.get("/", response_model=Events)
def read_events(
    session: SessionDep, current_user: CurrentUser, skip: int = 0, limit: int = 100
) -> Any:
    """
    Retrieve events.
    """

    if current_user.is_superuser:
        count_statement = select(func.count()).select_from(Event)
        count = session.exec(count_statement).one()
        statement = (
            select(Event).order_by(col(Event.created_at).desc()).offset(skip).limit(limit)
        )
        events = session.exec(statement).all()
    else:
        count_statement = (
            select(func.count())
            .select_from(Event)
            .where((Event.teacher_id == current_user.id) | (Event.student_id == current_user.id))
        )
        count = session.exec(count_statement).one()
        statement = (
            select(Event)
            .where((Event.teacher_id == current_user.id) | (Event.student_id == current_user.id))
            .order_by(col(Event.created_at).desc())
            .offset(skip)
            .limit(limit)
        )
        events = session.exec(statement).all()

    result = [Event.model_validate(item) for item in events]
    return Events(data=result, count=count)


@router.get("/{id}", response_model=Event)
def read_item(session: SessionDep, current_user: CurrentUser, id: uuid.UUID) -> Any:
    """
    Get event by ID.
    """
    event = session.get(Event, id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not current_user.is_superuser and (event.teacher_id != current_user.id and event.student_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return event


@router.post("/", response_model=Event)
def create_event(
    *, session: SessionDep, current_user: CurrentUser, event_in: EventCreate
) -> Any:
    """
    Create new event.
    """
    if current_user.is_teacher:
        update_dict = {"teacher_id": current_user.id, "student_id": current_user.id}
    else:
        update_dict = {
            "student_id": current_user.id,
            "teacher_id": current_user.teacher_id if current_user.teacher_id else current_user.id,
        }
    event = Event.model_validate(event_in, update=update_dict)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


@router.put("/{id}", response_model=Event)
def update_event(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    event_in: EventUpdate,
) -> Any:
    """
    Update an event.
    """
    event = session.get(Event, id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not current_user.is_superuser and (event.teacher_id != current_user.id and event.student_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    update_dict = event_in.model_dump(exclude_unset=True)
    event.sqlmodel_update(update_dict)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


@router.delete("/{id}")
def delete_event(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Message:
    """
    Delete an event.
    """
    event = session.get(Event, id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if not current_user.is_superuser and (event.teacher_id != current_user.id and event.student_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    session.delete(event)
    session.commit()
    return Message(message="Event deleted successfully")


@router.get("/recurrent/", response_model=RecurrentEvents)
def read_recurrent_events(
    session: SessionDep, current_user: CurrentUser, skip: int = 0, limit: int = 100
) -> Any:
    """
    Retrieve recurrent events.
    """
    if current_user.is_superuser:
        count_statement = select(func.count()).select_from(RecurrentEvent)
        count = session.exec(count_statement).one()
        statement = (
            select(RecurrentEvent)
            .order_by(col(RecurrentEvent.created_at).desc())
            .offset(skip)
            .limit(limit)
        )
        recurrent_events = session.exec(statement).all()
    else:
        count_statement = (
            select(func.count())
            .select_from(RecurrentEvent)
            .where(
                (RecurrentEvent.teacher_id == current_user.id)
                | (RecurrentEvent.student_id == current_user.id)
            )
        )
        count = session.exec(count_statement).one()
        statement = (
            select(RecurrentEvent)
            .where(
                (RecurrentEvent.teacher_id == current_user.id)
                | (RecurrentEvent.student_id == current_user.id)
            )
            .order_by(col(RecurrentEvent.created_at).desc())
            .offset(skip)
            .limit(limit)
        )
        recurrent_events = session.exec(statement).all()

    result = [RecurrentEvent.model_validate(item) for item in recurrent_events]
    return RecurrentEvents(data=result, count=count)


@router.get("/recurrent/{id}", response_model=RecurrentEvent)
def read_recurrent_event(session: SessionDep, current_user: CurrentUser, id: uuid.UUID) -> Any:
    """
    Get recurrent event by ID.
    """
    recurrent_event = session.get(RecurrentEvent, id)
    if not recurrent_event:
        raise HTTPException(status_code=404, detail="Recurrent event not found")
    condition = all([
        not current_user.is_superuser,
        (recurrent_event.teacher_id != current_user.id and recurrent_event.student_id != current_user.id),
    ])
    if condition:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return recurrent_event


@router.post("/recurrent", response_model=RecurrentEvent)
def create_recurrent_event(
    *, session: SessionDep, current_user: CurrentUser, recurrent_event_in: RecurrentEventCreate
) -> Any:
    """
    Create new recurrent event.
    """
    if current_user.is_teacher:
        update_dict = {"teacher_id": current_user.id, "student_id": current_user.id}
    else:
        update_dict = {
            "student_id": current_user.id,
            "teacher_id": current_user.teacher_id if current_user.teacher_id else current_user.id,
        }
    recurrent_event = RecurrentEvent.model_validate(recurrent_event_in, update=update_dict)
    session.add(recurrent_event)
    session.commit()
    session.refresh(recurrent_event)
    return recurrent_event


@router.put("/recurrent/{id}", response_model=RecurrentEvent)
def update_recurrent_event(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    id: uuid.UUID,
    recurrent_event_in: RecurrentEventUpdate,
) -> Any:
    """
    Update a recurrent event.
    """
    recurrent_event = session.get(RecurrentEvent, id)
    if not recurrent_event:
        raise HTTPException(status_code=404, detail="Recurrent event not found")
    condition = all([
        not current_user.is_superuser,
        (recurrent_event.teacher_id != current_user.id and recurrent_event.student_id != current_user.id),
    ])
    if condition:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    update_dict = recurrent_event_in.model_dump(exclude_unset=True)
    recurrent_event.sqlmodel_update(update_dict)
    session.add(recurrent_event)
    session.commit()
    session.refresh(recurrent_event)
    return recurrent_event


@router.delete("/recurrent/{id}")
def delete_recurrent_event(
    session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Message:
    """
    Delete a recurrent event.
    """
    recurrent_event = session.get(RecurrentEvent, id)
    if not recurrent_event:
        raise HTTPException(status_code=404, detail="Recurrent event not found")
    if not current_user.is_superuser and (recurrent_event.teacher_id != current_user.id and recurrent_event.student_id != current_user.id):
        raise HTTPException(status_code=403, detail="Not enough permissions")
    session.delete(recurrent_event)
    session.commit()
    return Message(message="Recurrent event deleted successfully")

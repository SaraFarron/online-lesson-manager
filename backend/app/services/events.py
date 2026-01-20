from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, RecurrentEvent, User
from app.repositories import EventRepository, RecurrentEventRepository
from app.schemas import EventCreate


class EventService:
    """Service for managing all kinds of events."""

    def __init__(self, session: AsyncSession) -> None:
        self.repository = EventRepository(session)
        self.recurrent_repo = RecurrentEventRepository(session)

    async def get_events_by_user(self, user: User) -> list[Event | RecurrentEvent]:
        """Get events for a specific user."""
        events =  await self.repository.get_by_user(user)
        recurrent_events = await self.recurrent_repo.get_by_user(user)
        return events + recurrent_events

    async def get_event_by_id(self, event_id: int, user: User) -> Event | None:
        """Get event by ID."""
        event = await self.repository.get_by_id(event_id)
        if event:
            if user.role == User.Roles.TEACHER and event.teacher_id == user.id:
                return event
            if user.role == User.Roles.STUDENT and event.student_id == user.id:
                return event
        return None

    async def get_recurrent_event_by_id(self, event_id: int, user: User) -> RecurrentEvent | None:
        """Get recurrent event by ID."""
        event = await self.recurrent_repo.get_by_id(event_id)
        if event:
            if user.role == User.Roles.TEACHER and event.teacher_id == user.id:
                return event
            if user.role == User.Roles.STUDENT and event.student_id == user.id:
                return event
        return None

    async def create_event(self, event: EventCreate, user: User) -> Event | RecurrentEvent:
        """Create a new event."""
        event_dict = event.to_dict(user)
        # TODO check for overlapping with other events
        if event.isRecurring:
            created_event = await self.recurrent_repo.create(event_dict)
        else:
            created_event = await self.repository.create(event_dict)
        return created_event

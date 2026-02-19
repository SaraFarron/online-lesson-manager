from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, RecurrentCancels, RecurrentEvent, User
from app.repositories.base import BaseRepository


class EventRepository(BaseRepository[Event]):
    """Repository for Event-specific database operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(Event, session)

    async def get_by_user(self, user: User) -> list[Event]:
        """Get all events for a specific user."""
        if user.role == User.Roles.TEACHER:
            query = select(Event).where(
                Event.teacher_id == user.id,
            )
        else:
            query = select(Event).where(
                Event.student_id == user.id,
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())


class RecurrentCancelsRepository(BaseRepository[RecurrentCancels]):
    """Repository for RecurrentEvent cancellation-specific database operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(RecurrentCancels, session)

    async def get_cancels_by_recurrent_events(self, recurrent_event_ids: list[int]) -> list[RecurrentCancels]:
        """Get all cancellations for specific recurrent events."""
        if not recurrent_event_ids:
            return []
        query = select(RecurrentCancels).where(
            RecurrentCancels.recurrent_event_id.in_(recurrent_event_ids),
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_event_and_date(self, recurrent_event_id: int, canceled_date: date) -> RecurrentCancels | None:
        """Get a cancellation for a specific recurrent event on a specific date.
        
        Args:
            recurrent_event_id: The ID of the recurrent event
            canceled_date: The date to check (time portion is ignored)
        
        Returns:
            RecurrentCancels object if found, None otherwise
        """
        query = select(RecurrentCancels).where(
            RecurrentCancels.recurrent_event_id == recurrent_event_id,
            func.date(RecurrentCancels.canceled_date) == canceled_date,
        )
        result = await self.session.execute(query)
        return result.scalars().first()


class RecurrentEventRepository(BaseRepository[RecurrentEvent]):
    """Repository for RecurrentEvent-specific database operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(RecurrentEvent, session)
        self.cancels_repo = RecurrentCancelsRepository(session)

    async def get_by_user(self, user: User) -> list[RecurrentEvent]:
        if user.role == User.Roles.TEACHER:
            query = select(RecurrentEvent).where(
                RecurrentEvent.teacher_id == user.id,
            )
        else:
            query = select(RecurrentEvent).where(RecurrentEvent.student_id == user.id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_for_date(self, user: User, target_date) -> list[RecurrentEvent]:
        """Get recurrent events that apply to a specific date, excluding cancellations.

        Args:
            user: The user to fetch events for
            target_date: The date to filter events by (datetime.date object)

        Returns:
            List of RecurrentEvent objects that occur on the specified date
        """
        # Get all user's recurrent events
        recurrent_events = await self.get_by_user(user)

        # Get cancellations for these events
        recurrent_cancels = await self.cancels_repo.get_cancels_by_recurrent_events(
            [event.id for event in recurrent_events]
        )

        # Filter out events cancelled on this date
        day_cancels = {
            cancel.recurrent_event_id for cancel in recurrent_cancels if cancel.canceled_date.date() == target_date
        }

        # Filter events that match weekday and are not cancelled
        matching_events = []
        for event in recurrent_events:
            # Skip if cancelled on this date
            if event.id in day_cancels:
                continue

            # Check if event started on or before the target date
            if event.start.date() > target_date:
                continue

            # Check if weekdays match
            if event.start.weekday() != target_date.weekday():
                continue

            # Check if event has ended (if interval_end is set)
            if event.interval_end and event.interval_end.date() < target_date:
                continue

            matching_events.append(event)

        return matching_events

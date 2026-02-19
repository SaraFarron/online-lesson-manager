from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, RecurrentEvent, User
from app.repositories import (
    EventRepository,
    RecurrentCancelsRepository,
    RecurrentEventRepository,
    TeacherSettingsRepository,
)
from app.schemas import EventCreate, EventUpdate


class EventService:
    """Service for managing all kinds of events."""

    def __init__(self, session: AsyncSession) -> None:
        self.repository = EventRepository(session)
        self.recurrent_repo = RecurrentEventRepository(session)
        self.cancels_repo = RecurrentCancelsRepository(session)
        self.teacher_settings_repo = TeacherSettingsRepository(session)

    async def get_events_by_user(self, user: User) -> list[Event | RecurrentEvent]:
        """Get events for a specific user."""
        events = await self.repository.get_by_user(user)
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

    async def delete_event(self, event_id: int, user: User):
        event = await self.repository.get_by_id(event_id)
        if event and (event.student_id == user.id or event.teacher_id == user.id):
            await self.repository.delete(event)
            return True
        return False

    async def delete_recurrent_event(self, event_id: int, user: User):
        event = await self.recurrent_repo.get_by_id(event_id)
        if event and (event.student_id == user.id or event.teacher_id == user.id):
            await self.recurrent_repo.delete(event)
            return True
        return False

    async def update_event(self, event: EventUpdate, user: User, event_id: int):
        existing_event = await self.repository.get_by_id(event_id)
        if not existing_event:
            return None
        event_dict = event.to_dict(user)
        return await self.repository.update(existing_event, event_dict)

    async def update_recurrent_event(self, event: EventUpdate, user: User, event_id: int):
        existing_event = await self.recurrent_repo.get_by_id(event_id)
        if not existing_event:
            return None
        event_dict = event.to_dict(user)
        return await self.recurrent_repo.update(existing_event, event_dict)

    async def check_slot_availability(
        self, user: User, check_date: date, start_time: time, end_time: time
    ) -> bool:
        """Check if a time slot is available on a specific date.
        
        This is the single source of truth for slot availability checking.
        Returns True if slot is free, False if occupied.
        Uses get_free_slots() to ensure consistency with schedule retrieval.
        """
        free_slots = await self.get_free_slots(user, check_date)
        for slot_start, slot_end in free_slots:
            if start_time >= slot_start and end_time <= slot_end:
                return True
        return False

    async def create_event(self, event: EventCreate, user: User) -> Event | RecurrentEvent:
        """Create a new event."""
        event_dict = event.to_dict(user)
        
        if event.isRecurring:
            # Check for overlapping with other events for next 2 months
            event_end = event.start + timedelta(minutes=event.duration)
            event_start_time = event.start.time()
            event_end_time = event_end.time()
            interval_end = event_dict.get("interval_end")
            
            # Generate occurrences for next 2 months or until interval_end, whichever is earlier
            two_months_from_start = event.start + timedelta(days=60)
            check_until = interval_end if interval_end and interval_end < two_months_from_start else two_months_from_start
            
            current_occurrence = event.start
            while current_occurrence < check_until:
                occurrence_date = current_occurrence.date()
                is_available = await self.check_slot_availability(
                    user, occurrence_date, event_start_time, event_end_time
                )
                if not is_available:
                    raise ValueError(f"The requested time slot is occupied on {occurrence_date.isoformat()}.")
                
                # Move to next occurrence
                current_occurrence += timedelta(days=event_dict.get("interval_days", 7))
            
            created_event = await self.recurrent_repo.create(event_dict)
        else:
            event_end = event.start + timedelta(minutes=event.duration)
            is_available = await self.check_slot_availability(
                user, event.start.date(), event.start.time(), event_end.time()
            )
            if not is_available:
                raise ValueError("The requested time slot is occupied.")
            created_event = await self.repository.create(event_dict)
        
        return created_event

    async def _get_occupied_slots_for_day(self, user: User, day: date) -> list[Event | RecurrentEvent]:
        """Get all events (regular + recurrent) that occupy time slots on a specific day.

        This is the single source of truth for determining what events occur on a given day.
        Used by both get_schedule() and get_free_slots() to ensure consistency.

        Args:
            user: The user to fetch events for
            day: The date to get events for

        Returns:
            List of Event and RecurrentEvent objects that occur on the specified date
        """
        events: list[Event | RecurrentEvent] = []

        # Get regular events for this day
        for event in await self.repository.get_by_user(user):
            if event.start.date() == day:
                events.append(event)

        # Get recurrent events for this day (with cancellation logic)
        recurrent_events = await self.recurrent_repo.get_for_date(user, day)
        events.extend(recurrent_events)

        return events

    async def get_schedule(self, user: User, day: date) -> list[Event | RecurrentEvent]:
        """Get schedule for a specific day."""
        return await self._get_occupied_slots_for_day(user, day)

    async def get_schedule_range(self, user: User, start_date: date, end_date: date):
        """Get schedule for a date range."""
        schedule: dict[str, list[Event | RecurrentEvent]] = {}
        current_date = start_date
        while current_date <= end_date:
            # Use centralized logic for getting events for each day
            schedule[current_date.isoformat()] = await self._get_occupied_slots_for_day(user, current_date)
            current_date += timedelta(days=1)
        return schedule

    @staticmethod
    def _filter_out_occupied_slots(
        events: list[Event | RecurrentEvent], start: time, end: time
    ) -> list[tuple[time, time]]:
        """Filter out occupied time slots from a list of events."""

        def is_occupied(slot_start: time, slot_end: time) -> bool:
            for event in events:
                event_start = event.start.time()
                event_end = event.end.time()
                if not (slot_end <= event_start or slot_start >= event_end):
                    return True
            return False

        free_slots: list[tuple[time, time]] = []
        current_start = start
        while current_start < end:
            current_end = (datetime.combine(date.min, current_start) + timedelta(minutes=5)).time()
            if current_end > end:
                current_end = end
            if not is_occupied(current_start, current_end):
                if free_slots and free_slots[-1][1] == current_start:
                    free_slots[-1] = (free_slots[-1][0], current_end)
                else:
                    free_slots.append((current_start, current_end))
            current_start = current_end
        return free_slots

    async def get_free_slots(self, user: User, day: date) -> list[tuple[time, time]]:
        """Get free time slots for a specific day."""
        if day < datetime.now(UTC).date():
            return []
        events = await self._get_occupied_slots_for_day(user, day)
        start, end = await self._get_working_hours(user)
        return self._filter_out_occupied_slots(events, start, end)

    async def _get_working_hours(self, user: User) -> tuple[time, time]:
        """Get working hours for a user."""
        teacher_settings = await self.teacher_settings_repo.get_by_user(user)
        if teacher_settings:
            return teacher_settings.work_start, teacher_settings.work_end
        return time(9, 0), time(17, 0)

    async def get_free_slots_range(
        self, user: User, start_date: date, end_date: date
    ) -> dict[str, list[dict[str, time]]]:
        """Get free time slots for a date range."""
        schedule = await self.get_schedule_range(user, start_date, end_date)
        free_slots_range: dict[str, list[dict[str, time]]] = {}
        start_work, end_work = await self._get_working_hours(user)
        for day_str, events in schedule.items():
            if date.fromisoformat(day_str) < datetime.now(UTC).date():
                free_slots_range[day_str] = []
            else:
                free_slots = self._filter_out_occupied_slots(events, start_work, end_work)
                free_slots_range[day_str] = [{"start": time_slot[0], "end": time_slot[1]} for time_slot in free_slots]
        return free_slots_range

    async def get_recurrent_free_slots(self, user: User, weekday: int) -> list[dict[str, time]]:
        """Get free time slots for a specific weekday (0=Monday, 6=Sunday)."""
        user_events = await self.recurrent_repo.get_by_user(user)
        recurrent_events = [event for event in user_events if event.start.weekday() == weekday]
        start, end = await self._get_working_hours(user)
        free_slots = self._filter_out_occupied_slots(recurrent_events, start, end)
        return [{"start": time_slot[0], "end": time_slot[1]} for time_slot in free_slots]

    async def cancel_recurrent_event(self, event_id: int, cancel_date: date, user: User) -> bool | str:
        """Cancel a specific occurrence of a recurrent event."""
        event = await self.recurrent_repo.get_by_id(event_id)
        if not event:
            return False
        if user.role == User.Roles.TEACHER and event.teacher_id != user.id:
            return False
        if user.role == User.Roles.STUDENT and event.student_id != user.id:
            return False
        
        # Check if cancellation already exists
        existing_cancel = await self.cancels_repo.get_by_event_and_date(event_id, cancel_date)
        if existing_cancel:
            return "A cancellation for this date already exists."
        
        # Check if the cancel_date is a valid occurrence of the recurrent event
        if cancel_date.weekday() != event.start.weekday() or cancel_date < event.start.date():
            return "Invalid cancellation date. It must match the weekday of the recurrent event and be in the future."
        
        now = datetime.now(UTC)
        # TODO 3 hours is hardcoded, should be configurable
        if cancel_date == now.date() and (now + timedelta(hours=3)).time() > event.start.time():
            return "Cannot cancel the event within 3 hours of its start time."

        await self.cancels_repo.create({"recurrent_event_id": event_id, "canceled_date": cancel_date})
        return True

from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, RecurrentEvent, User
from app.repositories import EventRepository, RecurrentCancelsRepository, RecurrentEventRepository
from app.schemas import EventCreate


class EventService:
    """Service for managing all kinds of events."""

    def __init__(self, session: AsyncSession) -> None:
        self.repository = EventRepository(session)
        self.recurrent_repo = RecurrentEventRepository(session)
        self.cancels_repo = RecurrentCancelsRepository(session)

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

    async def create_event(self, event: EventCreate, user: User) -> Event | RecurrentEvent:
        """Create a new event."""
        event_dict = event.to_dict(user)
        if event.isRecurring:
            # TODO check for overlapping with other events
            created_event = await self.recurrent_repo.create(event_dict)
        else:
            event_start = datetime.strptime(event.date + " " + event.startTime, "%Y-%m-%d %H:%M")
            event_end = event_start + timedelta(minutes=event.duration)
            free_slots = await self.get_free_slots(user, event_start.date())
            if (event_start.time(), event_end.time()) not in free_slots:
                raise ValueError("The requested time slot is occupied.")
            created_event = await self.repository.create(event_dict)
        return created_event

    async def get_schedule(self, user: User, day: date) -> list[Event | RecurrentEvent]:
        """Get schedule for a specific day."""
        events: list[Event | RecurrentEvent] = []
        for event in await self.repository.get_by_user(user):
            if event.start.date() == day:
                events.append(event)
        recurrent_events = await self.recurrent_repo.get_by_user(user)
        recurrent_cancels = await self.cancels_repo.get_cancels_by_recurrent_events(
            [event.id for event in recurrent_events]
        )
        day_cancels = {cancel.recurrent_event_id for cancel in recurrent_cancels if cancel.cancel_date.date() == day}
        recurrent_events = [event for event in recurrent_events if event.id not in day_cancels]
        for event in recurrent_events:
            if event.start.date() >= day and event.start.weekday() == day.weekday():
                events.append(event)
        return events

    async def get_schedule_range(self, user: User, start_date: date, end_date: date):
        """Get schedule for a date range."""
        events = await self.repository.get_by_user(user)
        recurrent_events = await self.recurrent_repo.get_by_user(user)
        recurrent_cancels = await self.cancels_repo.get_cancels_by_recurrent_events(
            [event.id for event in recurrent_events]
        )
        schedule: dict[str, list[Event | RecurrentEvent]] = {}
        current_date = start_date
        while current_date <= end_date:
            schedule[current_date.isoformat()] = []  # We need empty dates also
            for event in events:
                if event.start.date() == current_date:
                    schedule[current_date.isoformat()].append(event)
            day_cancels = {
                cancel.recurrent_event_id for cancel in recurrent_cancels if cancel.cancel_date.date() == current_date
            }
            recurrent_events_day = [event for event in recurrent_events if event.id not in day_cancels]
            for event in recurrent_events_day:
                if event.start.date() >= current_date and event.start.weekday() == current_date.weekday():
                    schedule[current_date.isoformat()].append(event)
            current_date += timedelta(days=1)
        return schedule

    @staticmethod
    def _filter_out_occupied_slots(events: list[Event | RecurrentEvent], start: time, end: time) -> list[tuple[time, time]]:
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
            current_end = (datetime.combine(date.min, current_start) + timedelta(hours=1)).time()
            if current_end > end:
                current_end = end
            if not is_occupied(current_start, current_end):
                free_slots.append((current_start, current_end))
            current_start = current_end
        return free_slots

    async def get_free_slots(self, user: User, day: date) -> list[tuple[time, time]]:
        """Get free time slots for a specific day."""
        # TODO time ranges only work in 60 minute duration and 0 minute start times
        events = await self.get_schedule(user, day)
        start, end = user.working_hours()
        return self._filter_out_occupied_slots(events, start, end)

    async def get_free_slots_range(
        self, user: User, start_date: date, end_date: date
    ) -> dict[str, list[tuple[time, time]]]:
        """Get free time slots for a date range."""
        schedule = await self.get_schedule_range(user, start_date, end_date)
        free_slots_range: dict[str, list[tuple[time, time]]] = {}
        start_work, end_work = user.working_hours()
        for day_str, events in schedule.items():
            free_slots = self._filter_out_occupied_slots(events, start_work, end_work)
            free_slots_range[day_str] = free_slots
        return free_slots_range


from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, RecurrentEvent, User
from app.repositories import EventRepository, RecurrentCancelsRepository, RecurrentEventRepository, TeacherSettingsRepository
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

    async def create_event(self, event: EventCreate, user: User) -> Event | RecurrentEvent:
        """Create a new event."""
        def is_overlapping(free_slots: list[tuple[time, time]], candidate: tuple[time, time]):
            for start, end in free_slots:
                if candidate[0] >= start and candidate[1] <= end:
                    return False
            return True

        event_dict = event.to_dict(user)
        if event.isRecurring:
            # TODO check for overlapping with other events
            created_event = await self.recurrent_repo.create(event_dict)
        else:
            event_start = datetime.strptime(
                " ".join([event.date, event.startTime]),
                "%Y-%m-%d %H:%M"
            )
            event_end = event_start + timedelta(minutes=event.duration)
            free_slots = await self.get_free_slots(user, event_start.date())
            if is_overlapping(free_slots, (event_start.time(), event_end.time())):
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
        events = await self.get_schedule(user, day)
        start, end = user.working_hours()
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
            if datetime.fromisoformat(day_str) < datetime.now():
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

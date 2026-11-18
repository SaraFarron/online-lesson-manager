import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from backend.auth.constants import Roles
from backend.auth.dependencies import CurrentUser, SessionDep
from backend.constants import EventType
from backend.events import availability
from backend.events.management.schemas import (
    AvailableEndTimes,
    AvailableStartTimes,
    LocalIntervalResolution,
    LocalIntervalResolutionRequest,
    ResolvedLocalInstant,
)
from backend.events.time_utils import normalize_utc, resolve_local_datetime
from backend.exceptions import Forbidden, ValidationError
from backend.schedule.router import ScheduleClockDep

router = APIRouter(prefix="/events", tags=["events"])


@router.post(
    "/resolve-local-time",
    response_model=LocalIntervalResolution,
    summary="Resolve a user-local event interval to UTC",
)
async def resolve_event_local_time(
    user: CurrentUser, data: LocalIntervalResolutionRequest
) -> LocalIntervalResolution:
    if user.role not in {Roles.TEACHER, Roles.STUDENT}:
        raise Forbidden("Only teachers and students can resolve event times")

    start_local = resolve_local_datetime(data.start, user.timezone)
    end_local = resolve_local_datetime(data.end, user.timezone)
    start_utc = normalize_utc(start_local)
    end_utc = normalize_utc(end_local)
    if start_utc >= end_utc:
        raise ValidationError("Resolved interval start must be before end")

    return LocalIntervalResolution(
        timezone=user.timezone,
        start=ResolvedLocalInstant.from_local(start_local, start_utc),
        end=ResolvedLocalInstant.from_local(end_local, end_utc),
    )


@router.get(
    "/available-start-times",
    response_model=AvailableStartTimes,
    summary="List advisory free start times for one viewer-local day",
    description=(
        "Returns clock-aligned local start times (at most one local day) that pass "
        "the same work-hour, limit, conflict and vacation rules a write would "
        "apply to a one-off event, excluding past times. Weekly lessons and breaks "
        "should use the same read: only the first occurrence is evaluated and "
        "later occurrences are still validated on write. "
        "Breaks are limited to the teacher's work hours and never overlap another "
        "event. `exclude_event_id` (optionally with `exclude_occurrence_start`) "
        "ignores an event being edited or moved. Results are advisory: they do not "
        "reserve a slot and a concurrent write can still conflict. Callers only "
        "learn whether their own booking would be accepted, never other events' "
        "details."
    ),
)
async def read_available_start_times(
    session: SessionDep,
    user: CurrentUser,
    now: ScheduleClockDep,
    day: Annotated[date, Query(alias="date", description="Viewer-local date.")],
    event_type: EventType = EventType.LESSON,
    duration_minutes: Annotated[int | None, Query(ge=1, le=1440)] = None,
    step_minutes: Annotated[
        int, Query(description="Grid step in minutes: 5 or 15.")
    ] = 15,
    subject_id: uuid.UUID | None = None,
    exclude_event_id: uuid.UUID | None = None,
    exclude_occurrence_start: datetime | None = None,
) -> AvailableStartTimes:
    return await availability.list_available_start_times(
        session,
        user,
        day=day,
        event_type=event_type,
        duration_minutes=duration_minutes,
        step_minutes=step_minutes,
        subject_id=subject_id,
        exclude_event_id=exclude_event_id,
        exclude_occurrence_start=exclude_occurrence_start,
        now=now,
    )


@router.get(
    "/available-break-end-times",
    response_model=AvailableEndTimes,
    summary="List advisory end times for a chosen break start",
    description=(
        "Returns each grid step after `start` up to the first overlap, the end of "
        "the teacher's work hours or local midnight. Teachers only. Advisory; the "
        "write path stays authoritative."
    ),
)
async def read_available_break_end_times(
    session: SessionDep,
    user: CurrentUser,
    start: Annotated[datetime, Query(description="Exact start instant (UTC).")],
    step_minutes: Annotated[
        int, Query(description="Grid step in minutes: 5 or 15.")
    ] = 15,
    exclude_event_id: uuid.UUID | None = None,
    exclude_occurrence_start: datetime | None = None,
) -> AvailableEndTimes:
    return await availability.list_available_break_end_times(
        session,
        user,
        start=start,
        step_minutes=step_minutes,
        exclude_event_id=exclude_event_id,
        exclude_occurrence_start=exclude_occurrence_start,
    )

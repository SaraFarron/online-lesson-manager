from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.auth.dependencies import CurrentUser, SessionDep
from backend.schedule import service as schedule_service
from backend.schedule.schemas import SchedulePeriod, ScheduleResponse

router = APIRouter(
    prefix="/schedule",
    tags=["schedule"],
    responses={
        400: {"description": "Inactive viewer or student without a teacher link"},
        401: {"description": "Missing or invalid user JWT"},
        403: {"description": "Viewer role is not allowed to view schedules"},
        409: {"description": "Linked teacher is missing, inactive, or not a teacher"},
        422: {"description": "Invalid date, timezone, event data, or schedule range"},
    },
)


def get_schedule_clock() -> datetime:
    """Return the UTC snapshot instant; override this dependency in deterministic tests."""
    return datetime.now(UTC)


ScheduleClockDep = Annotated[datetime, Depends(get_schedule_clock)]
DateQuery = Annotated[
    date | None,
    Query(
        alias="date",
        description=(
            "Viewer-local calendar date in YYYY-MM-DD format. If omitted, "
            "the current date is selected in the authenticated viewer's saved timezone."
        ),
        examples=["2026-10-19"],
    ),
]


@router.get(
    "/day",
    summary="Read one viewer-local schedule day",
    description=(
        "Returns one local calendar date in the authenticated user's saved IANA "
        "timezone. A teacher sees their own visible events and lesson subject labels; "
        "a linked student sees only their own visible events without subject labels. "
        "Events are included only on their local start date. The response contains "
        "one generated_at snapshot and UTC range/timestamp fields."
    ),
)
async def read_day(
    current_user: CurrentUser,
    session: SessionDep,
    generated_at: ScheduleClockDep,
    requested_date: DateQuery = None,
) -> ScheduleResponse:
    return await schedule_service.get_schedule(
        session,
        current_user,
        SchedulePeriod.DAY,
        requested_date,
        generated_at,
    )


@router.get(
    "/week",
    summary="Read the Monday-to-Sunday viewer-local schedule week",
    description=(
        "Returns the Monday-through-Sunday week containing the supplied viewer-local "
        "date, or the current local week if date is omitted. Uses the authenticated "
        "user's saved IANA timezone and the same role-scoped visibility rules as the "
        "day endpoint. Events are included only on their local start date. The response "
        "contains seven ascending local dates, including empty days, and one generated_at "
        "snapshot with UTC range/timestamp fields."
    ),
)
async def read_week(
    current_user: CurrentUser,
    session: SessionDep,
    generated_at: ScheduleClockDep,
    requested_date: DateQuery = None,
) -> ScheduleResponse:
    return await schedule_service.get_schedule(
        session,
        current_user,
        SchedulePeriod.WEEK,
        requested_date,
        generated_at,
    )

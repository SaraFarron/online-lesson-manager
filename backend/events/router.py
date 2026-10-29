from datetime import datetime

from fastapi import APIRouter

from backend.auth.constants import Roles
from backend.auth.dependencies import CurrentUser
from backend.events.management.schemas import (
    LocalIntervalResolution,
    LocalIntervalResolutionRequest,
    ResolvedLocalInstant,
)
from backend.events.time_utils import normalize_utc, resolve_local_datetime
from backend.exceptions import Forbidden, ValidationError

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
        start=_resolved_instant(start_local, start_utc),
        end=_resolved_instant(end_local, end_utc),
    )


def _resolved_instant(local: datetime, utc: datetime) -> ResolvedLocalInstant:
    offset = local.strftime("%z")
    return ResolvedLocalInstant(
        local=local,
        utc=utc,
        utc_offset=f"{offset[:3]}:{offset[3:]}",
    )

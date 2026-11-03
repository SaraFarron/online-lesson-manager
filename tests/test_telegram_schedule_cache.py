from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from telegram.api import (
    AUTHORIZATION_TTL_SECONDS,
    SCHEDULE_SNAPSHOT_TTL_SECONDS,
    ScheduleCacheIdentity,
    ScheduleResponse,
    ScheduleSnapshotCache,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _identity(
    *,
    backend: str = "https://api.example.test",
    sender_id: int = 5_000_000_001,
    account_id: int = 1,
    role: Literal["TEACHER", "STUDENT"] = "TEACHER",
    teacher_id: int | None = None,
    timezone: str = "UTC",
) -> ScheduleCacheIdentity:
    return ScheduleCacheIdentity(
        backend_namespace=backend,
        sender_id=sender_id,
        account_id=UUID(int=account_id),
        role=role,
        teacher_id=UUID(int=teacher_id) if teacher_id is not None else None,
        timezone=timezone,
    )


def _response(
    period: Literal["day", "week"] = "day",
    start_date: date = date(2026, 10, 19),
    timezone: str = "UTC",
    generated_at: datetime = datetime(2026, 10, 19, 12, tzinfo=UTC),
) -> ScheduleResponse:
    zone = ZoneInfo(timezone)
    day_count = 1 if period == "day" else 7
    range_start = datetime.combine(start_date, time.min, tzinfo=zone)
    range_end = datetime.combine(
        start_date + timedelta(days=day_count), time.min, tzinfo=zone
    )
    return ScheduleResponse.model_validate(
        {
            "period": period,
            "timezone": timezone,
            "generated_at": generated_at,
            "range_start": range_start,
            "range_end": range_end,
            "days": [
                {"date": start_date + timedelta(days=offset), "items": []}
                for offset in range(day_count)
            ],
        }
    )


def _authorized_cache(
    identity: ScheduleCacheIdentity,
    clock: _Clock,
    *,
    max_snapshots: int = 1_000,
) -> ScheduleSnapshotCache:
    cache = ScheduleSnapshotCache(max_snapshots=max_snapshots, clock=clock)
    ticket = cache.begin_authorization(identity.backend_namespace, identity.sender_id)
    assert cache.authorize(ticket, identity)
    return cache


def _store(
    cache: ScheduleSnapshotCache,
    identity: ScheduleCacheIdentity,
    response: ScheduleResponse,
) -> bool:
    ticket = cache.begin_fetch(identity, response.period)
    assert ticket is not None
    return cache.store(ticket, response)


def test_cache_isolation_includes_backend_sender_account_role_link_and_timezone() -> (
    None
):
    clock = _Clock()
    cache = ScheduleSnapshotCache(clock=clock)
    teacher = _identity()
    teacher_ticket = cache.begin_authorization(
        teacher.backend_namespace, teacher.sender_id
    )
    assert cache.authorize(teacher_ticket, teacher)
    assert _store(cache, teacher, _response())

    other_backend = _identity(backend="https://other.example.test")
    other_sender = _identity(sender_id=5_000_000_002)
    other_account = _identity(account_id=2)
    student = _identity(role="STUDENT", teacher_id=10, timezone="America/Los_Angeles")
    sender_auth = cache.begin_authorization(
        other_sender.backend_namespace, other_sender.sender_id
    )
    assert cache.authorize(sender_auth, other_sender)
    assert cache.get(other_sender, "day", date(2026, 10, 19)) is None

    for identity in (other_backend, other_account, student):
        assert cache.get(identity, "day", date(2026, 10, 19)) is None

    assert cache.get(teacher, "day", date(2026, 10, 19)) is not None


def test_changed_identity_invalidates_previous_snapshots_and_stale_auth_races() -> None:
    clock = _Clock()
    cache = ScheduleSnapshotCache(clock=clock)
    teacher = _identity()
    earlier = cache.begin_authorization(teacher.backend_namespace, teacher.sender_id)
    student = _identity(role="STUDENT", teacher_id=10, timezone="UTC")
    later = cache.begin_authorization(student.backend_namespace, student.sender_id)

    assert cache.authorize(later, student)
    assert not cache.authorize(earlier, teacher)
    assert cache.identity_for_sender(student.backend_namespace, student.sender_id) == (
        student
    )

    assert _store(cache, student, _response())
    changed_timezone = _identity(
        role="STUDENT",
        teacher_id=10,
        timezone="America/Los_Angeles",
    )
    changed = cache.begin_authorization(
        changed_timezone.backend_namespace, changed_timezone.sender_id
    )
    assert cache.authorize(changed, changed_timezone)
    assert cache.get(changed_timezone, "day", date(2026, 10, 19)) is None


def test_backend_environment_change_invalidates_and_fences_old_checks() -> None:
    clock = _Clock()
    cache = ScheduleSnapshotCache(clock=clock)
    original = _identity()
    original_ticket = cache.begin_authorization(
        original.backend_namespace, original.sender_id
    )
    assert cache.authorize(original_ticket, original)
    assert _store(cache, original, _response())
    old_inflight = cache.begin_authorization(
        original.backend_namespace, original.sender_id
    )

    changed_environment = _identity(backend="https://staging.example.test")
    new_ticket = cache.begin_authorization(
        changed_environment.backend_namespace, changed_environment.sender_id
    )
    assert cache.authorize(new_ticket, changed_environment)
    assert not cache.authorize(old_inflight, original)
    assert (
        cache.identity_for_sender(original.backend_namespace, original.sender_id)
        is None
    )
    assert (
        cache.identity_for_sender(
            changed_environment.backend_namespace, changed_environment.sender_id
        )
        == changed_environment
    )


def test_invalidation_fences_pending_checks_and_cancel_releases_tickets() -> None:
    clock = _Clock()
    cache = ScheduleSnapshotCache(clock=clock)
    identity = _identity()
    ticket = cache.begin_authorization(identity.backend_namespace, identity.sender_id)
    cache.invalidate(identity.backend_namespace, identity.sender_id)
    assert not cache.authorize(ticket, identity)

    cancelled = cache.begin_authorization(
        identity.backend_namespace, identity.sender_id
    )
    cache.cancel_authorization(cancelled)
    assert not cache.authorize(cancelled, identity)


def test_snapshot_expiry_is_strict_and_uses_monotonic_time(monkeypatch) -> None:
    clock = _Clock()
    identity = _identity()
    cache = _authorized_cache(identity, clock)
    assert _store(cache, identity, _response())

    monkeypatch.setattr("telegram.api.schedule_cache.time.time", lambda: -1e20)
    clock.value = 100.0 + SCHEDULE_SNAPSHOT_TTL_SECONDS - 0.001
    assert cache.get(identity, "day", date(2026, 10, 19)) is not None
    clock.value = 100.0 + SCHEDULE_SNAPSHOT_TTL_SECONDS
    assert cache.get(identity, "day", date(2026, 10, 19)) is None
    assert cache.identity_for_sender(identity.backend_namespace, identity.sender_id)


def test_authorization_expires_at_24_hours_and_clock_rollback_fails_closed() -> None:
    clock = _Clock()
    identity = _identity()
    cache = _authorized_cache(identity, clock)
    clock.value = 100.0 + AUTHORIZATION_TTL_SECONDS - 0.001
    assert cache.identity_for_sender(identity.backend_namespace, identity.sender_id)
    clock.value = 100.0 + AUTHORIZATION_TTL_SECONDS
    assert (
        cache.identity_for_sender(identity.backend_namespace, identity.sender_id)
        is None
    )

    clock = _Clock()
    cache = _authorized_cache(identity, clock)
    clock.value -= 1
    assert (
        cache.identity_for_sender(identity.backend_namespace, identity.sender_id)
        is None
    )


def test_only_latest_day_and_week_ranges_are_retained_per_identity() -> None:
    clock = _Clock()
    identity = _identity()
    cache = _authorized_cache(identity, clock)
    assert _store(cache, identity, _response("day", date(2026, 10, 19)))
    assert _store(cache, identity, _response("week", date(2026, 10, 19)))
    clock.value += 1
    assert _store(cache, identity, _response("day", date(2026, 10, 20)))

    assert cache.get(identity, "day", date(2026, 10, 19)) is None
    assert cache.get(identity, "day", date(2026, 10, 20)) is not None
    assert cache.get(identity, "week", date(2026, 10, 20)) is not None
    assert cache.get(identity, "week", date(2026, 10, 26)) is None


def test_global_snapshot_lru_bound_evicts_least_recently_used() -> None:
    clock = _Clock()
    first = _identity(account_id=1)
    second = _identity(sender_id=5_000_000_002, account_id=2)
    cache = ScheduleSnapshotCache(max_snapshots=2, clock=clock)
    for identity in (first, second):
        ticket = cache.begin_authorization(
            identity.backend_namespace, identity.sender_id
        )
        assert cache.authorize(ticket, identity)

    assert _store(cache, first, _response("day"))
    assert _store(cache, first, _response("week"))
    assert cache.get(first, "day", date(2026, 10, 19)) is not None
    assert _store(cache, second, _response("day"))

    assert cache.get(first, "day", date(2026, 10, 19)) is not None
    assert cache.get(first, "week", date(2026, 10, 19)) is None
    assert cache.get(second, "day", date(2026, 10, 19)) is not None


def test_identity_registry_is_bounded_and_eviction_removes_snapshots() -> None:
    clock = _Clock()
    first = _identity(account_id=1)
    second = _identity(sender_id=5_000_000_002, account_id=2)
    cache = ScheduleSnapshotCache(max_snapshots=1, clock=clock)
    first_ticket = cache.begin_authorization(first.backend_namespace, first.sender_id)
    assert cache.authorize(first_ticket, first)
    assert _store(cache, first, _response())

    second_ticket = cache.begin_authorization(
        second.backend_namespace, second.sender_id
    )
    assert cache.authorize(second_ticket, second)
    assert cache.identity_for_sender(first.backend_namespace, first.sender_id) is None
    assert cache.get(first, "day", date(2026, 10, 19)) is None


def test_older_schedule_response_cannot_overwrite_newer_inflight_request() -> None:
    clock = _Clock()
    identity = _identity()
    cache = _authorized_cache(identity, clock)
    older = cache.begin_fetch(identity, "day")
    assert older is not None
    clock.value += 1
    newer = cache.begin_fetch(identity, "day")
    assert newer is not None

    latest = _response(generated_at=datetime(2026, 10, 19, 13, tzinfo=UTC))
    stale = _response(generated_at=datetime(2026, 10, 19, 12, tzinfo=UTC))
    assert cache.store(newer, latest)
    assert not cache.store(older, stale)
    cached = cache.get(identity, "day", date(2026, 10, 19))
    assert cached is not None
    assert cached.response.generated_at == latest.generated_at


def test_failed_newer_request_does_not_prevent_older_success_from_being_cached() -> (
    None
):
    clock = _Clock()
    identity = _identity()
    cache = _authorized_cache(identity, clock)
    older = cache.begin_fetch(identity, "day")
    newer = cache.begin_fetch(identity, "day")
    assert older is not None
    assert newer is not None

    assert cache.store(older, _response())
    cached = cache.get(identity, "day", date(2026, 10, 19))
    assert cached is not None


@pytest.mark.parametrize(
    ("period", "start_date", "expected_hours"),
    [
        ("day", date(2026, 3, 8), 23),
        ("week", date(2026, 10, 26), 169),
    ],
)
def test_local_ranges_preserve_dst_boundaries(
    period: Literal["day", "week"],
    start_date: date,
    expected_hours: int,
) -> None:
    identity = _identity(timezone="America/New_York")
    clock = _Clock()
    cache = _authorized_cache(identity, clock)
    response = _response(period, start_date, identity.timezone)
    assert response.range_end.astimezone(UTC) - response.range_start.astimezone(
        UTC
    ) == timedelta(hours=expected_hours)
    assert _store(cache, identity, response)
    assert cache.get(identity, period, start_date) is not None


def test_invalid_period_timezone_and_actual_range_are_not_stored() -> None:
    clock = _Clock()
    identity = _identity()
    cache = _authorized_cache(identity, clock)

    period_ticket = cache.begin_fetch(identity, "week")
    assert period_ticket is not None
    with pytest.raises(ValueError, match="period"):
        cache.store(period_ticket, _response("day"))

    timezone_ticket = cache.begin_fetch(identity, "day")
    assert timezone_ticket is not None
    with pytest.raises(ValueError, match="timezone"):
        cache.store(
            timezone_ticket,
            _response(timezone="America/Los_Angeles"),
        )

    range_ticket = cache.begin_fetch(identity, "day")
    assert range_ticket is not None
    invalid_range = _response().model_copy(
        update={"range_end": datetime(2026, 10, 21, tzinfo=UTC)}
    )
    with pytest.raises(ValueError, match="range"):
        cache.store(range_ticket, invalid_range)

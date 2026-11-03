from __future__ import annotations

import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models.schedule import ScheduleResponse

SchedulePeriod = Literal["day", "week"]
ScheduleRole = Literal["TEACHER", "STUDENT"]

AUTHORIZATION_TTL_SECONDS = 24 * 60 * 60
SCHEDULE_SNAPSHOT_TTL_SECONDS = 60 * 60
MAX_SCHEDULE_SNAPSHOTS = 1_000


@dataclass(frozen=True)
class ScheduleCacheIdentity:
    backend_namespace: str
    sender_id: int
    account_id: UUID
    role: ScheduleRole
    teacher_id: UUID | None
    timezone: str

    def __post_init__(self) -> None:
        if not self.backend_namespace or self.backend_namespace.strip() != (
            self.backend_namespace
        ):
            raise ValueError("A backend namespace is required")
        if type(self.sender_id) is not int or self.sender_id <= 0:
            raise ValueError("A positive Telegram sender ID is required")
        if self.role not in {"TEACHER", "STUDENT"}:
            raise ValueError("Only teacher and student schedules can be cached")
        if not isinstance(self.account_id, UUID):
            raise ValueError("A valid account ID is required")
        if self.role == "TEACHER" and self.teacher_id is not None:
            raise ValueError("Teacher cache identities cannot have a teacher link")
        if self.role == "STUDENT" and self.teacher_id is None:
            raise ValueError("Student cache identities require a teacher link")
        if self.teacher_id is not None and not isinstance(self.teacher_id, UUID):
            raise ValueError("A valid teacher link is required")
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("A valid viewer timezone is required") from exc


@dataclass(frozen=True)
class AuthorizationTicket:
    backend_namespace: str
    sender_id: int
    sequence: int
    generation: int
    started_at: float


@dataclass(frozen=True)
class ScheduleFetchTicket:
    identity: ScheduleCacheIdentity
    period: SchedulePeriod
    sequence: int
    started_at: float


@dataclass(frozen=True)
class CachedScheduleSnapshot:
    response: ScheduleResponse
    fetched_at: float
    authorization_checked_at: float


@dataclass(frozen=True)
class _Principal:
    backend_namespace: str
    sender_id: int


@dataclass(frozen=True)
class _Authorization:
    identity: ScheduleCacheIdentity
    checked_at: float
    sequence: int


@dataclass(frozen=True)
class _ScheduleKey:
    identity: ScheduleCacheIdentity
    period: SchedulePeriod
    local_start: date
    local_end: date
    range_start: datetime
    range_end: datetime


class ScheduleSnapshotCache:
    def __init__(
        self,
        *,
        max_snapshots: int = MAX_SCHEDULE_SNAPSHOTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_snapshots < 1:
            raise ValueError("The schedule cache must allow at least one snapshot")
        self.max_snapshots = max_snapshots
        self._clock = clock
        self._sequence = 0
        self._authorizations: OrderedDict[_Principal, _Authorization] = OrderedDict()
        self._authorization_generations: dict[_Principal, int] = {}
        self._pending_authorizations: dict[_Principal, set[int]] = {}
        self._snapshots: OrderedDict[_ScheduleKey, CachedScheduleSnapshot] = (
            OrderedDict()
        )
        self._slots: dict[
            tuple[ScheduleCacheIdentity, SchedulePeriod], _ScheduleKey
        ] = {}
        self._committed_fetches: dict[
            tuple[ScheduleCacheIdentity, SchedulePeriod], int
        ] = {}

    def begin_authorization(
        self, backend_namespace: str, sender_id: int
    ) -> AuthorizationTicket:
        self._validate_principal(backend_namespace, sender_id)
        principal = _Principal(backend_namespace, sender_id)
        sequence = self._next_sequence()
        self._pending_authorizations.setdefault(principal, set()).add(sequence)
        return AuthorizationTicket(
            backend_namespace,
            sender_id,
            sequence,
            self._authorization_generations.get(principal, 0),
            self._now(),
        )

    def authorize(
        self,
        ticket: AuthorizationTicket,
        identity: ScheduleCacheIdentity,
    ) -> bool:
        principal = _Principal(ticket.backend_namespace, ticket.sender_id)
        if (
            identity.backend_namespace != ticket.backend_namespace
            or identity.sender_id != ticket.sender_id
        ):
            self._finish_authorization(principal, ticket.sequence)
            self._cleanup_authorization_generation(principal)
            raise ValueError("Authorization ticket does not match the account identity")

        now = self._now()
        generation = self._authorization_generations.get(principal, 0)
        if (
            not self._finish_authorization(principal, ticket.sequence)
            or ticket.generation != generation
            or now < ticket.started_at
        ):
            self._cleanup_authorization_generation(principal)
            return False

        for known_principal in set(self._authorizations) | set(
            self._pending_authorizations
        ):
            if (
                known_principal.sender_id == identity.sender_id
                and known_principal.backend_namespace != identity.backend_namespace
            ):
                self._remove_principal(known_principal)

        current = self._authorizations.get(principal)
        if current is not None and current.sequence > ticket.sequence:
            self._cleanup_authorization_generation(principal)
            return False
        if current is not None and current.identity != identity:
            self._remove_principal_snapshots(principal)
            generation += 1
            self._authorization_generations[principal] = generation

        self._authorizations[principal] = _Authorization(
            identity=identity,
            checked_at=now,
            sequence=ticket.sequence,
        )
        self._authorizations.move_to_end(principal)
        self._enforce_authorization_bound()
        return principal in self._authorizations

    def cancel_authorization(self, ticket: AuthorizationTicket) -> None:
        principal = _Principal(ticket.backend_namespace, ticket.sender_id)
        self._finish_authorization(principal, ticket.sequence)
        self._cleanup_authorization_generation(principal)

    def identity_for_sender(
        self, backend_namespace: str, sender_id: int
    ) -> ScheduleCacheIdentity | None:
        self._validate_principal(backend_namespace, sender_id)
        principal = _Principal(backend_namespace, sender_id)
        authorization = self._authorizations.get(principal)
        if authorization is None:
            return None
        now = self._now()
        if not self._is_fresh(authorization.checked_at, AUTHORIZATION_TTL_SECONDS, now):
            self._remove_principal(principal)
            return None
        self._authorizations.move_to_end(principal)
        return authorization.identity

    def invalidate(self, backend_namespace: str, sender_id: int) -> None:
        self._validate_principal(backend_namespace, sender_id)
        self._remove_principal(_Principal(backend_namespace, sender_id))

    def begin_fetch(
        self, identity: ScheduleCacheIdentity, period: SchedulePeriod
    ) -> ScheduleFetchTicket | None:
        self._validate_period(period)
        authorization = self._fresh_authorization(identity)
        if authorization is None:
            return None
        return ScheduleFetchTicket(
            identity=identity,
            period=period,
            sequence=self._next_sequence(),
            started_at=self._now(),
        )

    def store(
        self,
        ticket: ScheduleFetchTicket,
        response: ScheduleResponse,
    ) -> bool:
        now = self._now()
        if now < ticket.started_at:
            return False
        authorization = self._fresh_authorization(ticket.identity, now=now)
        if authorization is None:
            return False

        validated = ScheduleResponse.model_validate(response.model_dump(mode="python"))
        key = self._make_key(ticket.identity, ticket.period, validated)
        slot = (ticket.identity, ticket.period)
        if ticket.sequence <= self._committed_fetches.get(slot, -1):
            return False

        existing_key = self._slots.get(slot)
        if existing_key is not None and existing_key != key:
            self._remove_snapshot(existing_key)

        self._snapshots[key] = CachedScheduleSnapshot(
            response=validated.model_copy(deep=True),
            fetched_at=now,
            authorization_checked_at=authorization.checked_at,
        )
        self._snapshots.move_to_end(key)
        self._slots[slot] = key
        self._committed_fetches[slot] = ticket.sequence
        self._enforce_snapshot_bound()
        return key in self._snapshots

    def get(
        self,
        identity: ScheduleCacheIdentity,
        period: SchedulePeriod,
        requested_date: date,
    ) -> CachedScheduleSnapshot | None:
        self._validate_period(period)
        if isinstance(requested_date, datetime):
            raise ValueError("Requested schedule date must be a local date")
        if self._fresh_authorization(identity) is None:
            return None

        slot = (identity, period)
        key = self._slots.get(slot)
        if key is None:
            return None
        snapshot = self._snapshots.get(key)
        if snapshot is None:
            self._slots.pop(slot, None)
            return None

        now = self._now()
        if not self._is_fresh(snapshot.fetched_at, SCHEDULE_SNAPSHOT_TTL_SECONDS, now):
            self._remove_snapshot(key)
            return None

        expected_date = requested_date
        if period == "week":
            expected_date -= timedelta(days=expected_date.weekday())
        if snapshot.response.days[0].date != expected_date:
            return None

        self._snapshots.move_to_end(key)
        self._authorizations.move_to_end(self._principal(identity))
        return CachedScheduleSnapshot(
            response=snapshot.response.model_copy(deep=True),
            fetched_at=snapshot.fetched_at,
            authorization_checked_at=snapshot.authorization_checked_at,
        )

    def _fresh_authorization(
        self, identity: ScheduleCacheIdentity, *, now: float | None = None
    ) -> _Authorization | None:
        principal = self._principal(identity)
        authorization = self._authorizations.get(principal)
        if authorization is None:
            return None
        if authorization.identity != identity:
            return None
        current_time = self._now() if now is None else now
        if not self._is_fresh(
            authorization.checked_at, AUTHORIZATION_TTL_SECONDS, current_time
        ):
            self._remove_principal(principal)
            return None
        self._authorizations.move_to_end(principal)
        return authorization

    def _make_key(
        self,
        identity: ScheduleCacheIdentity,
        period: SchedulePeriod,
        response: ScheduleResponse,
    ) -> _ScheduleKey:
        if response.period != period:
            raise ValueError("Schedule period does not match its request")
        if response.timezone != identity.timezone:
            raise ValueError("Schedule timezone does not match its viewer identity")

        zone = ZoneInfo(identity.timezone)
        local_start = response.range_start.astimezone(zone).date()
        local_end = response.range_end.astimezone(zone).date()
        expected_days = 1 if period == "day" else 7
        first_date = response.days[0].date
        last_date = response.days[-1].date
        if (
            local_start != first_date
            or local_end != last_date + timedelta(days=1)
            or len(response.days) != expected_days
        ):
            raise ValueError("Schedule range does not match its local dates")

        return _ScheduleKey(
            identity=identity,
            period=period,
            local_start=first_date,
            local_end=last_date,
            range_start=response.range_start.astimezone(UTC),
            range_end=response.range_end.astimezone(UTC),
        )

    def _enforce_authorization_bound(self) -> None:
        while len(self._authorizations) > self.max_snapshots:
            principal = next(iter(self._authorizations))
            self._remove_principal(principal)

    def _enforce_snapshot_bound(self) -> None:
        while len(self._snapshots) > self.max_snapshots:
            key, _ = self._snapshots.popitem(last=False)
            slot = (key.identity, key.period)
            if self._slots.get(slot) == key:
                self._slots.pop(slot, None)

    def _remove_snapshot(self, key: _ScheduleKey) -> None:
        self._snapshots.pop(key, None)
        slot = (key.identity, key.period)
        if self._slots.get(slot) == key:
            self._slots.pop(slot, None)

    def _remove_principal_snapshots(self, principal: _Principal) -> None:
        for key in tuple(self._snapshots):
            if self._principal(key.identity) == principal:
                self._remove_snapshot(key)
        for slot in tuple(self._committed_fetches):
            if self._principal(slot[0]) == principal:
                self._committed_fetches.pop(slot, None)

    def _remove_principal(self, principal: _Principal) -> None:
        self._authorizations.pop(principal, None)
        self._remove_principal_snapshots(principal)
        if self._pending_authorizations.get(principal):
            self._authorization_generations[principal] = (
                self._authorization_generations.get(principal, 0) + 1
            )
        else:
            self._authorization_generations.pop(principal, None)

    def _finish_authorization(self, principal: _Principal, sequence: int) -> bool:
        pending = self._pending_authorizations.get(principal)
        if pending is None or sequence not in pending:
            return False
        pending.remove(sequence)
        if not pending:
            self._pending_authorizations.pop(principal, None)
        return True

    def _cleanup_authorization_generation(self, principal: _Principal) -> None:
        if (
            principal not in self._authorizations
            and principal not in self._pending_authorizations
        ):
            self._authorization_generations.pop(principal, None)

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _now(self) -> float:
        now = self._clock()
        if not math.isfinite(now):
            raise ValueError("Schedule cache clock must return a finite value")
        return now

    @staticmethod
    def _principal(identity: ScheduleCacheIdentity) -> _Principal:
        return _Principal(identity.backend_namespace, identity.sender_id)

    @staticmethod
    def _validate_principal(backend_namespace: str, sender_id: int) -> None:
        if not backend_namespace or backend_namespace.strip() != backend_namespace:
            raise ValueError("A backend namespace is required")
        if type(sender_id) is not int or sender_id <= 0:
            raise ValueError("A positive Telegram sender ID is required")

    @staticmethod
    def _validate_period(period: str) -> None:
        if period not in {"day", "week"}:
            raise ValueError("Schedule period must be day or week")

    @staticmethod
    def _is_fresh(created_at: float, ttl: float, now: float) -> bool:
        age = now - created_at
        return 0 <= age < ttl

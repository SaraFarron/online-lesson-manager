from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import pytest

from telegram.api import (
    BackendClient,
    BackendContractError,
    BackendError,
    BackendUnavailable,
    CircuitBreaker,
    ScheduleSnapshotCache,
)

SENDER_ID = 5_000_000_001
ACCOUNT_ID = UUID(int=1)
REQUESTED_DATE = date(2026, 10, 19)


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class _Backend:
    def __init__(self) -> None:
        self.outage_path: str | None = None
        self.outage: str | None = None
        self.malformed_path: str | None = None
        self.account_status: int | None = None
        self.session_status: int | None = None
        self.schedule_status: int | None = None
        self.schedule_generated_at = datetime(2026, 10, 19, 12, tzinfo=UTC)
        self.account_role = "TEACHER"
        self.teacher_id: UUID | None = None
        self.requests: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if path == self.outage_path:
            if self.outage == "transport":
                raise httpx.ConnectError("test outage", request=request)
            if self.outage == "server":
                return httpx.Response(503)
        if path == self.malformed_path:
            return httpx.Response(200, json={})
        if path == "/api/v1/auth/me":
            if self.account_status is not None:
                return httpx.Response(self.account_status, json={"detail": "denied"})
            return httpx.Response(200, json=self.account_payload())
        if path == "/api/v1/telegram/session":
            status = self.session_status if self.session_status is not None else 404
            return httpx.Response(status, json={"detail": "session failed"})
        if path == "/api/v1/schedule/day":
            if self.schedule_status is not None:
                return httpx.Response(
                    self.schedule_status, json={"detail": "schedule failed"}
                )
            return httpx.Response(
                200,
                json=self.schedule_payload(),
            )
        return httpx.Response(404, json={"detail": "not found"})

    def account_payload(self) -> dict[str, object]:
        return {
            "id": str(ACCOUNT_ID),
            "full_name": "Schedule User",
            "role": self.account_role,
            "timezone": "UTC",
            "teacher_id": (
                str(self.teacher_id) if self.teacher_id is not None else None
            ),
            "notification_lessons_today": None,
            "is_active": True,
        }

    def schedule_payload(self) -> dict[str, object]:
        zone = ZoneInfo("UTC")
        return {
            "period": "day",
            "timezone": "UTC",
            "generated_at": self.schedule_generated_at.isoformat(),
            "range_start": datetime.combine(
                REQUESTED_DATE, time.min, tzinfo=zone
            ).isoformat(),
            "range_end": datetime.combine(
                REQUESTED_DATE + timedelta(days=1), time.min, tzinfo=zone
            ).isoformat(),
            "days": [{"date": REQUESTED_DATE.isoformat(), "items": []}],
        }


def _client(
    backend: _Backend,
    clock: _Clock,
) -> tuple[httpx.AsyncClient, BackendClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(backend),
        base_url="https://api.example.test",
    )
    breaker = CircuitBreaker(threshold=1, recovery=30, clock=clock)
    api = BackendClient(
        http,
        "test-service-key",
        breaker=breaker,
        schedule_cache=ScheduleSnapshotCache(clock=clock),
    )
    api.tokens[SENDER_ID] = "test-user-token"
    return http, api


async def _fetch_live(api: BackendClient):
    return await api.read_schedule_view(
        SENDER_ID,
        SENDER_ID,
        "day",
        requested_date=REQUESTED_DATE,
    )


@pytest.mark.parametrize(
    ("outage", "path"),
    [
        ("transport", "/api/v1/auth/me"),
        ("server", "/api/v1/auth/me"),
        ("transport", "/api/v1/schedule/day"),
        ("server", "/api/v1/schedule/day"),
    ],
)
async def test_transport_and_server_failures_use_eligible_cached_schedule(
    outage: str,
    path: str,
) -> None:
    backend = _Backend()
    clock = _Clock()
    http, api = _client(backend, clock)
    try:
        live = await _fetch_live(api)
        assert live.source == "live"
        backend.outage = outage
        backend.outage_path = path

        cached = await _fetch_live(api)
        assert cached.source == "cached"
        assert cached.response.days[0].date == REQUESTED_DATE
    finally:
        await http.aclose()


async def test_open_circuit_uses_eligible_cached_schedule() -> None:
    backend = _Backend()
    clock = _Clock()
    http, api = _client(backend, clock)
    try:
        assert (await _fetch_live(api)).source == "live"
        api.breaker.opened_at = clock.value
        clock.value += 1

        cached = await _fetch_live(api)

        assert cached.source == "cached"
        assert backend.requests == ["/api/v1/auth/me", "/api/v1/schedule/day"]
    finally:
        await http.aclose()


@pytest.mark.parametrize("status", [400, 403, 404, 409, 422])
async def test_4xx_access_failure_never_falls_back_and_evicts_cache(
    status: int,
) -> None:
    backend = _Backend()
    clock = _Clock()
    http, api = _client(backend, clock)
    try:
        assert (await _fetch_live(api)).source == "live"
        backend.schedule_status = status
        with pytest.raises(BackendError) as error:
            await _fetch_live(api)

        assert error.value.status == status
        assert (
            api.schedule_cache.identity_for_sender(api._backend_namespace, SENDER_ID)
            is None
        )
    finally:
        await http.aclose()


async def test_auth_renewal_outage_uses_cache_but_definitive_session_failure_evicts() -> (
    None
):
    backend = _Backend()
    clock = _Clock()
    http, api = _client(backend, clock)
    try:
        assert (await _fetch_live(api)).source == "live"
        backend.account_status = 401
        backend.session_status = 503

        cached = await _fetch_live(api)

        assert cached.source == "cached"
        backend.session_status = 404
        clock.value += 31
        with pytest.raises(BackendError):
            await _fetch_live(api)
        assert (
            api.schedule_cache.identity_for_sender(api._backend_namespace, SENDER_ID)
            is None
        )
    finally:
        await http.aclose()


@pytest.mark.parametrize("malformed_path", ["/api/v1/auth/me", "/api/v1/schedule/day"])
async def test_malformed_account_or_schedule_response_never_uses_cache(
    malformed_path: str,
) -> None:
    backend = _Backend()
    clock = _Clock()
    http, api = _client(backend, clock)
    try:
        assert (await _fetch_live(api)).source == "live"
        backend.malformed_path = malformed_path

        with pytest.raises(BackendContractError):
            await _fetch_live(api)
        assert api.breaker.opened_at is None
    finally:
        await http.aclose()


async def test_cache_miss_and_expired_snapshot_remain_unavailable() -> None:
    backend = _Backend()
    clock = _Clock()
    http, api = _client(backend, clock)
    try:
        backend.outage = "server"
        backend.outage_path = "/api/v1/auth/me"
        with pytest.raises(BackendUnavailable):
            await _fetch_live(api)

        clock.value += 31
        backend.outage = None
        backend.outage_path = None
        assert (await _fetch_live(api)).source == "live"
        clock.value += 60 * 60
        backend.outage = "server"
        backend.outage_path = "/api/v1/schedule/day"
        with pytest.raises(BackendUnavailable):
            await _fetch_live(api)
    finally:
        await http.aclose()


async def test_live_success_replaces_cached_snapshot_for_exact_range() -> None:
    backend = _Backend()
    clock = _Clock()
    http, api = _client(backend, clock)
    try:
        first = await _fetch_live(api)
        backend.schedule_generated_at += timedelta(minutes=5)
        clock.value += 10
        refreshed = await _fetch_live(api)
        assert first.source == "live"
        assert refreshed.source == "live"
        backend.outage = "transport"
        backend.outage_path = "/api/v1/auth/me"
        cached = await _fetch_live(api)

        assert cached.source == "cached"
        assert cached.response.generated_at == refreshed.response.generated_at
    finally:
        await http.aclose()


async def test_live_schedule_uses_role_from_revalidated_account() -> None:
    backend = _Backend()
    clock = _Clock()
    http, api = _client(backend, clock)
    try:
        teacher_result = await _fetch_live(api)
        assert teacher_result.role == "TEACHER"

        backend.account_role = "STUDENT"
        backend.teacher_id = UUID(int=2)
        student_result = await _fetch_live(api)

        assert student_result.source == "live"
        assert student_result.role == "STUDENT"
        identity = api.offline_schedule_identity(SENDER_ID)
        assert identity is not None
        assert identity.role == "STUDENT"
    finally:
        await http.aclose()

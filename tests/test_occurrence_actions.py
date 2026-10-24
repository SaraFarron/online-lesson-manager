from datetime import datetime
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.events.models import Event, RecurrentCancels, RecurrentEvent


def _headers(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


async def _register_and_login(
    client: AsyncClient, email: str, role: str
) -> tuple[str, str]:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "role": role,
            "timezone": "UTC",
        },
    )
    assert registration.status_code == 201, registration.text
    login = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "password123"},
    )
    assert login.status_code == 200, login.text
    return registration.json()["id"], login.json()["access_token"]


async def _teacher_and_students(
    client: AsyncClient, suffix: str
) -> tuple[str, str, tuple[str, str], tuple[str, str]]:
    teacher_id, teacher_token = await _register_and_login(
        client, f"{suffix}-teacher@test.com", "TEACHER"
    )
    code = f"t_{suffix.upper()[:6]}"
    profile = await client.post(
        f"/api/v1/auth/users/{teacher_id}/teacher-profile",
        headers=_headers(teacher_token),
        json={"code": code, "working_days": [1, 2, 3, 4, 5, 6, 7]},
    )
    assert profile.status_code == 201, profile.text

    students: list[tuple[str, str]] = []
    for label in ("a", "b"):
        student_id, student_token = await _register_and_login(
            client, f"{suffix}-student-{label}@test.com", "STUDENT"
        )
        linked = await client.post(
            "/api/v1/auth/me/teacher",
            headers=_headers(student_token),
            json={"teacher_code": code},
        )
        assert linked.status_code == 200, linked.text
        students.append((student_id, student_token))
    return teacher_id, teacher_token, students[0], students[1]


async def _create_series(
    client: AsyncClient, teacher_token: str, student_id: str
) -> str:
    response = await client.post(
        "/api/v1/recurrent-lessons/",
        headers=_headers(teacher_token),
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T09:30:00Z",
            "user_id": student_id,
            "interval": 7,
            "interval_end": "2026-12-29T09:00:00Z",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_exact_occurrence_cancel_is_authorized_and_rejects_repeats(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, teacher_token, student_a, student_b = await _teacher_and_students(
        client, "actions-cancel"
    )
    series_id = await _create_series(client, teacher_token, student_a[0])

    request = {
        "occurrence_start": "2026-12-08T09:00:00Z",
        "occurrence_end": "2026-12-08T09:30:00Z",
    }
    cancelled = await client.post(
        f"/api/v1/recurrent-lessons/{series_id}/occurrences/cancel",
        headers=_headers(teacher_token),
        json=request,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["cancelled_occurrence"] == {
        "start": request["occurrence_start"],
        "end": request["occurrence_end"],
    }
    assert len(cancelled.json()["revision"]) == 64

    persisted = await db_session.scalar(
        select(RecurrentCancels).where(
            RecurrentCancels.recurrent_event_id == UUID(series_id)
        )
    )
    assert persisted is not None
    assert persisted.start == datetime(2026, 12, 8, 9)
    assert persisted.end == datetime(2026, 12, 8, 9, 30)

    repeated = await client.post(
        f"/api/v1/recurrent-lessons/{series_id}/occurrences/cancel",
        headers=_headers(teacher_token),
        json=request,
    )
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "occurrence_already_cancelled"

    off_grid = await client.post(
        f"/api/v1/recurrent-lessons/{series_id}/occurrences/cancel",
        headers=_headers(teacher_token),
        json={
            "occurrence_start": "2026-12-08T09:15:00Z",
            "occurrence_end": "2026-12-08T09:45:00Z",
        },
    )
    assert off_grid.status_code == 422

    cross_student = await client.post(
        f"/api/v1/recurrent-lessons/{series_id}/occurrences/cancel",
        headers=_headers(student_b[1]),
        json={
            "occurrence_start": "2026-12-15T09:00:00Z",
            "occurrence_end": "2026-12-15T09:30:00Z",
        },
    )
    assert cross_student.status_code == 403

    own_occurrence = await client.post(
        f"/api/v1/recurrent-lessons/{series_id}/occurrences/cancel",
        headers=_headers(student_a[1]),
        json={
            "occurrence_start": "2026-12-15T09:00:00Z",
            "occurrence_end": "2026-12-15T09:30:00Z",
        },
    )
    assert own_occurrence.status_code == 200, own_occurrence.text
    cancellations = (
        await db_session.scalars(
            select(RecurrentCancels)
            .where(RecurrentCancels.recurrent_event_id == UUID(series_id))
            .order_by(RecurrentCancels.start)
        )
    ).all()
    assert [item.start for item in cancellations] == [
        datetime(2026, 12, 8, 9),
        datetime(2026, 12, 15, 9),
    ]


async def test_occurrence_move_is_atomic_on_validation_failure(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, teacher_token, student_a, student_b = await _teacher_and_students(
        client, "actions-move"
    )
    series_id = await _create_series(client, teacher_token, student_a[0])
    blocking_lesson = await client.post(
        "/api/v1/lessons/",
        headers=_headers(teacher_token),
        json={
            "start": "2026-12-08T11:00:00Z",
            "end": "2026-12-08T11:30:00Z",
            "user_id": student_b[0],
        },
    )
    assert blocking_lesson.status_code == 201, blocking_lesson.text

    failed_move = await client.post(
        f"/api/v1/recurrent-lessons/{series_id}/occurrences/move",
        headers=_headers(teacher_token),
        json={
            "occurrence_start": "2026-12-08T09:00:00Z",
            "occurrence_end": "2026-12-08T09:30:00Z",
            "replacement_start": "2026-12-08T11:00:00Z",
            "replacement_end": "2026-12-08T11:30:00Z",
        },
    )
    assert failed_move.status_code == 422
    assert (
        await db_session.scalar(
            select(RecurrentCancels).where(
                RecurrentCancels.recurrent_event_id == UUID(series_id)
            )
        )
        is None
    )
    assert (
        await db_session.scalar(
            select(Event).where(
                Event.user_id == UUID(student_a[0]),
                Event.start == datetime(2026, 12, 8, 11),
            )
        )
        is None
    )

    moved = await client.post(
        f"/api/v1/recurrent-lessons/{series_id}/occurrences/move",
        headers=_headers(teacher_token),
        json={
            "occurrence_start": "2026-12-08T09:00:00Z",
            "occurrence_end": "2026-12-08T09:30:00Z",
            "replacement_start": "2026-12-08T10:00:00Z",
            "replacement_end": "2026-12-08T10:30:00Z",
        },
    )
    assert moved.status_code == 201, moved.text
    result = moved.json()
    assert result["cancelled_occurrence"] == {
        "start": "2026-12-08T09:00:00Z",
        "end": "2026-12-08T09:30:00Z",
    }
    assert result["replacement"]["start"] == "2026-12-08T10:00:00+00:00"
    assert result["replacement"]["end"] == "2026-12-08T10:30:00+00:00"
    assert result["replacement"]["user_id"] == student_a[0]
    assert len(result["revision"]) == 64
    assert (
        await db_session.scalar(
            select(RecurrentCancels).where(
                RecurrentCancels.recurrent_event_id == UUID(series_id)
            )
        )
        is not None
    )


async def test_if_match_rejects_stale_event_edits(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, teacher_token, student, _ = await _teacher_and_students(
        client, "actions-revision"
    )
    created = await client.post(
        "/api/v1/lessons/",
        headers=_headers(teacher_token),
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T09:30:00Z",
            "user_id": student[0],
        },
    )
    assert created.status_code == 201, created.text
    lesson_id = created.json()["id"]
    detail = await client.get(
        f"/api/v1/lessons/{lesson_id}", headers=_headers(teacher_token)
    )
    assert detail.status_code == 200, detail.text
    revision = detail.json()["revision"]

    updated = await client.put(
        f"/api/v1/lessons/{lesson_id}",
        headers=_headers(teacher_token, **{"If-Match": revision}),
        json={
            "start": "2026-12-01T10:00:00Z",
            "end": "2026-12-01T10:30:00Z",
            "user_id": student[0],
        },
    )
    assert updated.status_code == 200, updated.text

    stale = await client.put(
        f"/api/v1/lessons/{lesson_id}",
        headers=_headers(teacher_token, **{"If-Match": revision}),
        json={
            "start": "2026-12-01T11:00:00Z",
            "end": "2026-12-01T11:30:00Z",
            "user_id": student[0],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_event_revision"
    persisted = await db_session.get(Event, UUID(lesson_id))
    assert persisted is not None
    assert persisted.start == datetime(2026, 12, 1, 10)

    stale_delete = await client.delete(
        f"/api/v1/lessons/{lesson_id}",
        headers=_headers(teacher_token, **{"If-Match": revision}),
    )
    assert stale_delete.status_code == 409
    current_detail = await client.get(
        f"/api/v1/lessons/{lesson_id}", headers=_headers(teacher_token)
    )
    deleted = await client.delete(
        f"/api/v1/lessons/{lesson_id}",
        headers=_headers(
            teacher_token,
            **{"If-Match": current_detail.json()["revision"]},
        ),
    )
    assert deleted.status_code == 204
    assert (
        await db_session.scalar(select(Event).where(Event.id == UUID(lesson_id)))
        is None
    )

    series_id = await _create_series(client, teacher_token, student[0])
    series_detail = await client.get(
        f"/api/v1/recurrent-lessons/{series_id}",
        headers=_headers(teacher_token),
    )
    assert series_detail.status_code == 200, series_detail.text
    series_revision = series_detail.json()["revision"]
    series_payload = {
        "start": "2026-12-01T11:00:00Z",
        "end": "2026-12-01T11:30:00Z",
        "user_id": student[0],
        "interval": 7,
        "interval_end": "2026-12-29T11:00:00Z",
    }
    series_updated = await client.put(
        f"/api/v1/recurrent-lessons/{series_id}",
        headers=_headers(teacher_token, **{"If-Match": series_revision}),
        json=series_payload,
    )
    assert series_updated.status_code == 200, series_updated.text
    stale_series_update = await client.put(
        f"/api/v1/recurrent-lessons/{series_id}",
        headers=_headers(teacher_token, **{"If-Match": series_revision}),
        json={
            **series_payload,
            "start": "2026-12-01T12:00:00Z",
            "end": "2026-12-01T12:30:00Z",
            "interval_end": "2026-12-29T12:00:00Z",
        },
    )
    assert stale_series_update.status_code == 409
    assert stale_series_update.json()["detail"]["code"] == "stale_event_revision"
    persisted_series = await db_session.get(RecurrentEvent, UUID(series_id))
    assert persisted_series is not None
    assert persisted_series.start == datetime(2026, 12, 1, 11)


async def test_break_displacement_confirmation_is_bound_to_preview(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher_id, teacher_token, student_a, _ = await _teacher_and_students(
        client, "actions-break"
    )
    first = await client.post(
        "/api/v1/lessons/",
        headers=_headers(teacher_token),
        json={
            "start": "2026-12-01T09:00:00Z",
            "end": "2026-12-01T09:30:00Z",
            "user_id": student_a[0],
        },
    )
    assert first.status_code == 201, first.text
    break_payload = {
        "start": "2026-12-01T09:00:00Z",
        "end": "2026-12-01T10:00:00Z",
        "user_id": teacher_id,
    }
    preview = await client.post(
        "/api/v1/breaks/",
        headers=_headers(teacher_token),
        json=break_payload,
    )
    assert preview.status_code == 409, preview.text
    first_token = preview.json()["detail"]["confirmation_token"]

    changed_request = {
        **break_payload,
        "start": "2026-12-01T09:01:00Z",
        "end": "2026-12-01T10:01:00Z",
    }
    changed_confirmation = await client.post(
        "/api/v1/breaks/?confirm_displacement=true",
        headers=_headers(teacher_token, **{"X-Displacement-Token": first_token}),
        json=changed_request,
    )
    assert changed_confirmation.status_code == 409
    assert changed_confirmation.json()["detail"]["confirmation_token"] != first_token
    assert await db_session.get(Event, UUID(first.json()["id"])) is not None

    second = await client.post(
        "/api/v1/lessons/",
        headers=_headers(teacher_token),
        json={
            "start": "2026-12-01T09:30:00Z",
            "end": "2026-12-01T10:00:00Z",
            "user_id": student_a[0],
        },
    )
    assert second.status_code == 201, second.text

    stale_confirmation = await client.post(
        "/api/v1/breaks/?confirm_displacement=true",
        headers=_headers(teacher_token, **{"X-Displacement-Token": first_token}),
        json=break_payload,
    )
    assert stale_confirmation.status_code == 409
    fresh_preview = stale_confirmation.json()["detail"]
    assert fresh_preview["confirmation_token"] != first_token
    assert len(fresh_preview["affected_occurrences"]) == 2
    assert await db_session.get(Event, UUID(first.json()["id"])) is not None
    assert await db_session.get(Event, UUID(second.json()["id"])) is not None

    confirmed = await client.post(
        "/api/v1/breaks/?confirm_displacement=true",
        headers=_headers(
            teacher_token,
            **{"X-Displacement-Token": fresh_preview["confirmation_token"]},
        ),
        json=break_payload,
    )
    assert confirmed.status_code == 201, confirmed.text
    assert await db_session.get(Event, UUID(first.json()["id"])) is None
    assert await db_session.get(Event, UUID(second.json()["id"])) is None

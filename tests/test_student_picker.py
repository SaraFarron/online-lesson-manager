from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import User
from backend.events.utils import assert_can_assign_user
from backend.exceptions import Forbidden


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


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_teacher_student_picker_paginates_and_filters_inactive_and_unlinked(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    teacher_id, teacher_token = await _register_and_login(
        client, "picker-teacher@test.com", "TEACHER"
    )
    students = [
        User(
            full_name=f"Student {letter}",
            role="STUDENT",
            timezone="UTC",
            teacher_id=UUID(teacher_id),
        )
        for letter in "ABCD"
    ]
    inactive = User(
        full_name="Inactive",
        role="STUDENT",
        timezone="UTC",
        teacher_id=UUID(teacher_id),
        is_active=False,
    )
    unlinked = User(
        full_name="Unlinked",
        role="STUDENT",
        timezone="UTC",
    )
    db_session.add_all([*students, inactive, unlinked])
    await db_session.commit()

    first_page = await client.get(
        "/api/v1/auth/teacher/students",
        params={"skip": 0, "limit": 2},
        headers=_headers(teacher_token),
    )
    second_page = await client.get(
        "/api/v1/auth/teacher/students",
        params={"skip": 2, "limit": 2},
        headers=_headers(teacher_token),
    )
    assert first_page.status_code == 200, first_page.text
    assert second_page.status_code == 200, second_page.text
    assert first_page.json()["total"] == 4
    assert second_page.json()["total"] == 4
    first_items = first_page.json()["items"]
    second_items = second_page.json()["items"]
    assert [item["display_label"] for item in first_items] == [
        "Student A",
        "Student B",
    ]
    assert [item["display_label"] for item in second_items] == [
        "Student C",
        "Student D",
    ]
    assert all(set(item) == {"id", "display_label"} for item in first_items)
    assert not {inactive.id, unlinked.id} & {
        UUID(item["id"]) for item in first_items + second_items
    }

    teacher = await db_session.get(User, UUID(teacher_id))
    assert teacher is not None
    for target in (inactive, unlinked):
        with pytest.raises(Forbidden):
            await assert_can_assign_user(db_session, teacher, target.id)


async def test_student_picker_enforces_role_and_returns_empty_unrelated_roster(
    client: AsyncClient,
) -> None:
    _, student_token = await _register_and_login(
        client, "picker-student@test.com", "STUDENT"
    )
    student_response = await client.get(
        "/api/v1/auth/teacher/students", headers=_headers(student_token)
    )
    assert student_response.status_code == 403

    _, unrelated_teacher_token = await _register_and_login(
        client, "picker-unrelated-teacher@test.com", "TEACHER"
    )
    unrelated_response = await client.get(
        "/api/v1/auth/teacher/students",
        headers=_headers(unrelated_teacher_token),
    )
    assert unrelated_response.status_code == 200, unrelated_response.text
    assert unrelated_response.json() == {"items": [], "total": 0}


async def test_teacher_student_picker_rejects_unbounded_page_size(
    client: AsyncClient,
) -> None:
    _, teacher_token = await _register_and_login(
        client, "picker-bounded-teacher@test.com", "TEACHER"
    )
    response = await client.get(
        "/api/v1/auth/teacher/students",
        params={"limit": 101},
        headers=_headers(teacher_token),
    )
    assert response.status_code == 422

from httpx import AsyncClient


async def _register_and_login(
    client: AsyncClient, email: str, role: str
) -> tuple[str, str]:
    register_resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "password123",
            "role": role,
            "timezone": "UTC",
        },
    )
    assert register_resp.status_code == 201, register_resp.text
    user_id = register_resp.json()["id"]

    login_resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": "password123"},
    )
    assert login_resp.status_code == 200, login_resp.text
    token = login_resp.json()["access_token"]
    return user_id, token


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_lesson_lifecycle_with_ownership_checks(client: AsyncClient) -> None:
    """End-to-end smoke test exercising Tasks 1-4: teacher/student linking,
    role-aware teacher_id resolution, the self-conflict update fix, and the
    ownership/IDOR checks on update and delete.
    """
    # Task 1: student-teacher linking via teacher code.
    teacher_id, teacher_token = await _register_and_login(
        client, "teacher@test.com", "TEACHER"
    )
    profile_resp = await client.post(
        f"/api/v1/auth/users/{teacher_id}/teacher-profile",
        json={"code": "JOINCODE1"},
        headers=_auth_header(teacher_token),
    )
    assert profile_resp.status_code == 201, profile_resp.text

    student_id, student_token = await _register_and_login(
        client, "student@test.com", "STUDENT"
    )
    link_resp = await client.post(
        "/api/v1/auth/me/teacher",
        json={"teacher_code": "JOINCODE1"},
        headers=_auth_header(student_token),
    )
    assert link_resp.status_code == 200, link_resp.text

    # Task 2: teacher creates a lesson for their own student (role-aware teacher_id).
    create_resp = await client.post(
        "/api/v1/lessons/",
        json={
            "start": "2026-12-01T09:00:00",
            "end": "2026-12-01T09:30:00",
            "user_id": student_id,
            "event_type": "LESSON",
        },
        headers=_auth_header(teacher_token),
    )
    assert create_resp.status_code == 201, create_resp.text
    lesson_id = create_resp.json()["id"]

    # Task 3: updating a lesson to a range overlapping its own old range must not
    # false-positive as a scheduling conflict.
    update_resp = await client.put(
        f"/api/v1/lessons/{lesson_id}",
        json={
            "start": "2026-12-01T09:05:00",
            "end": "2026-12-01T09:35:00",
            "user_id": student_id,
            "event_type": "LESSON",
        },
        headers=_auth_header(teacher_token),
    )
    assert update_resp.status_code == 200, update_resp.text

    # Task 4: an unrelated teacher must not be able to update or delete this lesson.
    _, other_teacher_token = await _register_and_login(
        client, "other_teacher@test.com", "TEACHER"
    )
    idor_update_resp = await client.put(
        f"/api/v1/lessons/{lesson_id}",
        json={
            "start": "2026-12-02T09:00:00",
            "end": "2026-12-02T09:30:00",
            "user_id": student_id,
            "event_type": "LESSON",
        },
        headers=_auth_header(other_teacher_token),
    )
    assert idor_update_resp.status_code == 403, idor_update_resp.text

    idor_delete_resp = await client.delete(
        f"/api/v1/lessons/{lesson_id}",
        headers=_auth_header(other_teacher_token),
    )
    assert idor_delete_resp.status_code == 403, idor_delete_resp.text

    # The legitimate owner can still delete their own lesson.
    delete_resp = await client.delete(
        f"/api/v1/lessons/{lesson_id}",
        headers=_auth_header(teacher_token),
    )
    assert delete_resp.status_code == 204, delete_resp.text

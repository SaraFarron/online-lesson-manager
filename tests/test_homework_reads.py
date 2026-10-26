from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import User
from backend.auth.utils import create_access_token
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.models import Homework, HomeworkAttachment
from backend.homework.read_service import list_homework
from backend.main import app


@pytest.fixture
def homework_storage(tmp_path: Path) -> Generator[Path]:
    config = HomeworkConfig(STORAGE_PATH=tmp_path)
    app.dependency_overrides[get_homework_settings] = lambda: config
    try:
        yield tmp_path
    finally:
        app.dependency_overrides.pop(get_homework_settings, None)


async def _user(session: AsyncSession, role: str, name: str) -> User:
    user = User(
        id=uuid4(),
        email=f"{uuid4()}@example.com",
        full_name=name,
        role=role,
        timezone="UTC",
    )
    session.add(user)
    await session.commit()
    return user


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token}"}


async def _assignment(
    session: AsyncSession,
    teacher: User,
    student: User,
    *,
    text: str,
    status: str = "INCOMPLETE",
) -> Homework:
    homework = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        text=text,
        status=status,
        completion_source="TEACHER" if status == "COMPLETE" else None,
        completed_at=datetime.now(UTC) if status == "COMPLETE" else None,
        operation_key=uuid4(),
    )
    session.add(homework)
    await session.commit()
    return homework


async def test_lists_are_bounded_paginated_and_role_scoped(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER", "Teacher")
    student = await _user(db_session, "STUDENT", "Student")
    student.teacher_id = teacher.id
    await db_session.commit()
    first = await _assignment(
        db_session, teacher, student, text="First", status="COMPLETE"
    )
    second = await _assignment(db_session, teacher, student, text="Second")

    page = await client.get(
        "/api/v1/homework/assignments?skip=0&limit=1",
        headers=_auth(teacher),
    )
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1
    assert page.json()["items"][0]["id"] == str(second.id)
    assert page.json()["items"][0]["student"] == {
        "id": str(student.id),
        "full_name": "Student",
    }

    next_page = await client.get(
        "/api/v1/homework/assignments?skip=1&limit=1",
        headers=_auth(teacher),
    )
    assert next_page.status_code == 200
    assert next_page.json()["total"] == 2
    assert next_page.json()["items"][0]["id"] == str(first.id)
    assert next_page.json()["items"][0]["status"] == "COMPLETE"
    assert next_page.json()["items"][0]["completion_source"] == "TEACHER"

    completed = await client.get(
        "/api/v1/homework/assignments?status=COMPLETE",
        headers=_auth(teacher),
    )
    assert completed.status_code == 200
    assert completed.json()["total"] == 1
    assert completed.json()["items"][0]["id"] == str(first.id)

    student_page = await client.get(
        "/api/v1/homework/assignments", headers=_auth(student)
    )
    assert student_page.status_code == 200
    assert student_page.json()["total"] == 2
    assert all(item["student"] is None for item in student_page.json()["items"])

    unknown_student = await _user(db_session, "STUDENT", "Other student")
    invisible = await client.get(
        "/api/v1/homework/assignments", headers=_auth(unknown_student)
    )
    assert invisible.status_code == 403


async def test_teacher_student_filter_is_scoped_to_visible_assignments(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER", "Teacher")
    student = await _user(db_session, "STUDENT", "Student")
    student.teacher_id = teacher.id
    await db_session.commit()
    visible = await _assignment(db_session, teacher, student, text="Visible")

    other_teacher = await _user(db_session, "TEACHER", "Other teacher")
    other_student = await _user(db_session, "STUDENT", "Other student")
    other_student.teacher_id = other_teacher.id
    await db_session.commit()
    await _assignment(db_session, other_teacher, other_student, text="Private")

    selected = await client.get(
        f"/api/v1/homework/assignments?student_id={student.id}",
        headers=_auth(teacher),
    )
    assert selected.status_code == 200
    assert selected.json()["total"] == 1
    assert selected.json()["items"][0]["id"] == str(visible.id)

    unrelated_student = await client.get(
        f"/api/v1/homework/assignments?student_id={other_student.id}",
        headers=_auth(teacher),
    )
    assert unrelated_student.status_code == 200
    assert unrelated_student.json()["total"] == 0

    forged_for_student = await client.get(
        f"/api/v1/homework/assignments?student_id={other_student.id}",
        headers=_auth(student),
    )
    assert forged_for_student.status_code == 200
    assert forged_for_student.json()["total"] == 0


async def test_detail_returns_only_assignment_material_metadata(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER", "Teacher")
    student = await _user(db_session, "STUDENT", "Student")
    student.teacher_id = teacher.id
    await db_session.commit()
    homework = await _assignment(db_session, teacher, student, text="Read this")
    lesson_source_id = uuid4()
    homework.lesson_kind = "RECURRENT_EVENT"
    homework.lesson_source_id = lesson_source_id
    homework.lesson_occurrence_start = datetime(2030, 10, 1, 9, 0)
    attachment = HomeworkAttachment(
        owner_user_id=teacher.id,
        homework_id=homework.id,
        storage_key=uuid4().hex,
        file_name="lesson.pdf",
        byte_size=123,
        staged_until=None,
    )
    db_session.add(attachment)
    await db_session.commit()

    teacher_detail = await client.get(
        f"/api/v1/homework/assignments/{homework.id}",
        headers=_auth(teacher),
    )
    assert teacher_detail.status_code == 200
    assert teacher_detail.json()["student"]["id"] == str(student.id)
    assert teacher_detail.json()["text"] == "Read this"
    assert teacher_detail.json()["material_count"] == 1
    assert teacher_detail.json()["lesson_occurrence"] == {
        "kind": "RECURRENT_EVENT",
        "source_id": str(lesson_source_id),
        "starts_at": "2030-10-01T09:00:00+00:00",
    }
    assert teacher_detail.json()["materials"] == [
        {
            "id": str(attachment.id),
            "file_name": "lesson.pdf",
            "byte_size": 123,
        }
    ]
    assert "storage_key" not in teacher_detail.text

    student_detail = await client.get(
        f"/api/v1/homework/assignments/{homework.id}",
        headers=_auth(student),
    )
    assert student_detail.status_code == 200
    assert student_detail.json()["student"] is None
    assert student_detail.json()["materials"] == teacher_detail.json()["materials"]

    other_teacher = await _user(db_session, "TEACHER", "Other teacher")
    other_student = await _user(db_session, "STUDENT", "Other student")
    other_student.teacher_id = other_teacher.id
    await db_session.commit()
    for user in (other_teacher, other_student):
        response = await client.get(
            f"/api/v1/homework/assignments/{homework.id}",
            headers=_auth(user),
        )
        assert response.status_code == 404
    forged = await client.get(
        f"/api/v1/homework/assignments/{uuid4()}",
        headers=_auth(teacher),
    )
    assert forged.status_code == 404


async def test_link_changes_and_soft_deletion_revoke_reads(
    client: AsyncClient,
    db_session: AsyncSession,
    homework_storage: Path,
) -> None:
    original_teacher = await _user(db_session, "TEACHER", "Original")
    new_teacher = await _user(db_session, "TEACHER", "New")
    student = await _user(db_session, "STUDENT", "Student")
    student.teacher_id = original_teacher.id
    await db_session.commit()
    old_assignment = await _assignment(
        db_session, original_teacher, student, text="Old work"
    )
    old_attachment = HomeworkAttachment(
        owner_user_id=original_teacher.id,
        homework_id=old_assignment.id,
        storage_key=uuid4().hex,
        file_name="old-material.pdf",
        byte_size=len(b"private material"),
        staged_until=None,
    )
    (homework_storage / old_attachment.storage_key).write_bytes(b"private material")
    db_session.add(old_attachment)
    await db_session.commit()
    for user in (original_teacher, student):
        response = await client.get(
            f"/api/v1/homework/attachments/{old_attachment.id}",
            headers=_auth(user),
        )
        assert response.status_code == 200

    student.teacher_id = new_teacher.id
    await db_session.commit()
    old_teacher_list = await client.get(
        "/api/v1/homework/assignments", headers=_auth(original_teacher)
    )
    assert old_teacher_list.status_code == 200
    assert old_teacher_list.json()["total"] == 0
    new_teacher_list = await client.get(
        "/api/v1/homework/assignments", headers=_auth(new_teacher)
    )
    assert new_teacher_list.status_code == 200
    assert new_teacher_list.json()["total"] == 0

    for user in (original_teacher, new_teacher, student):
        response = await client.get(
            f"/api/v1/homework/assignments/{old_assignment.id}",
            headers=_auth(user),
        )
        assert response.status_code == 404
        attachment_response = await client.get(
            f"/api/v1/homework/attachments/{old_attachment.id}",
            headers=_auth(user),
        )
        assert attachment_response.status_code == 404

    hidden_assignment = await _assignment(
        db_session, new_teacher, student, text="New work"
    )
    assert (
        await client.get(
            f"/api/v1/homework/assignments/{hidden_assignment.id}",
            headers=_auth(new_teacher),
        )
    ).status_code == 200
    hidden_assignment.deleted_at = datetime.now(UTC)
    await db_session.commit()
    assert (
        await client.get(
            f"/api/v1/homework/assignments/{hidden_assignment.id}",
            headers=_auth(new_teacher),
        )
    ).status_code == 404


async def test_teacher_list_query_count_does_not_grow_with_page_size(
    db_session: AsyncSession,
) -> None:
    teacher = await _user(db_session, "TEACHER", "Teacher")
    student = await _user(db_session, "STUDENT", "Student")
    student.teacher_id = teacher.id
    await db_session.commit()
    for index in range(30):
        db_session.add(
            Homework(
                teacher_id=teacher.id,
                student_id=student.id,
                text=f"Assignment {index}",
                operation_key=uuid4(),
            )
        )
    await db_session.commit()

    connection = db_session.sync_session.bind
    assert connection is not None
    statement_count = 0

    def count_statements(*args: object) -> None:
        nonlocal statement_count
        statement = args[2]
        if isinstance(statement, str) and statement.lstrip().upper().startswith(
            "SELECT"
        ):
            statement_count += 1

    event.listen(connection, "before_cursor_execute", count_statements)
    try:
        result = await list_homework(
            db_session,
            teacher,
            skip=0,
            limit=100,
            status_filter=None,
        )
    finally:
        event.remove(connection, "before_cursor_execute", count_statements)
    assert result.total == 30
    assert len(result.items) == 30
    assert statement_count == 2

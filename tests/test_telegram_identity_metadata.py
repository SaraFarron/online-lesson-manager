from sqlalchemy import select

from backend.auth import service as auth_service
from backend.auth.models import (
    StudentTeacherRelationship,
    TeacherProfile,
    User,
)
from telegram.api import BackendClient


async def test_telegram_session_updates_or_clears_actual_username(
    client, db_session, bot_service_key
) -> None:
    user = User(
        role="STUDENT",
        timezone="UTC",
        telegram_user_id=9_000_000_001,
        telegram_private_chat_id=9_000_000_001,
        telegram_username="Old_Name",
    )
    db_session.add(user)
    await db_session.commit()

    headers = {"X-Bot-Service-Key": bot_service_key}
    endpoint = "/api/v1/telegram/session"
    payload = {
        "telegram_user_id": user.telegram_user_id,
        "telegram_private_chat_id": user.telegram_private_chat_id,
        "telegram_username": "New_Name",
    }
    response = await client.post(endpoint, headers=headers, json=payload)
    assert response.status_code == 200
    await db_session.refresh(user)
    assert user.telegram_username == "New_Name"

    payload["telegram_username"] = None
    response = await client.post(endpoint, headers=headers, json=payload)
    assert response.status_code == 200
    await db_session.refresh(user)
    assert user.telegram_username is None

    user.telegram_username = "Known_Name"
    await db_session.commit()
    payload.pop("telegram_username")
    response = await client.post(endpoint, headers=headers, json=payload)
    assert response.status_code == 200
    await db_session.refresh(user)
    assert user.telegram_username == "Known_Name"

    payload["telegram_username"] = "invalid username"
    response = await client.post(endpoint, headers=headers, json=payload)
    assert response.status_code == 422
    await db_session.refresh(user)
    assert user.telegram_username == "Known_Name"


async def test_telegram_student_registration_records_username_and_link(
    client, db_session, bot_service_key
) -> None:
    teacher = User(role="TEACHER", timezone="UTC", full_name="Teacher")
    db_session.add(teacher)
    await db_session.flush()
    db_session.add(TeacherProfile(teacher_id=teacher.id, code="t_TEACH1"))
    await db_session.commit()

    response = await client.post(
        "/api/v1/telegram/register",
        headers={"X-Bot-Service-Key": bot_service_key},
        json={
            "telegram_user_id": 9_000_000_002,
            "telegram_private_chat_id": 9_000_000_002,
            "telegram_username": "Student_1",
            "role": "STUDENT",
            "timezone": "UTC",
            "teacher_code": "t_TEACH1",
        },
    )
    assert response.status_code == 201

    student = await db_session.scalar(
        select(User).where(User.telegram_user_id == 9_000_000_002)
    )
    assert student is not None
    assert student.telegram_username == "Student_1"
    assert student.teacher_id == teacher.id
    relationship = await db_session.scalar(
        select(StudentTeacherRelationship).where(
            StudentTeacherRelationship.student_id == student.id
        )
    )
    assert relationship is not None
    assert relationship.teacher_id == teacher.id
    assert relationship.linked_at is not None


async def test_invitation_link_records_association_date(db_session) -> None:
    teacher = User(role="TEACHER", timezone="UTC")
    student = User(role="STUDENT", timezone="UTC")
    db_session.add_all([teacher, student])
    await db_session.flush()
    db_session.add(TeacherProfile(teacher_id=teacher.id, code="t_TEACH3"))
    await db_session.commit()

    await auth_service.link_student_to_teacher(db_session, student, "t_TEACH3")

    relationship = await db_session.scalar(
        select(StudentTeacherRelationship).where(
            StudentTeacherRelationship.student_id == student.id
        )
    )
    assert relationship is not None
    assert relationship.teacher_id == teacher.id
    assert relationship.linked_at is not None


async def test_bot_login_sends_sender_username_including_absence(
    client, db_session, bot_service_key
) -> None:
    teacher = User(
        role="TEACHER",
        timezone="UTC",
        telegram_user_id=9_000_000_003,
        telegram_private_chat_id=9_000_000_003,
        telegram_username="Old_Name",
    )
    db_session.add(teacher)
    await db_session.flush()
    db_session.add(TeacherProfile(teacher_id=teacher.id, code="t_TEACH2"))
    await db_session.commit()

    api = BackendClient(client, bot_service_key)
    view = await api.login(
        teacher.telegram_user_id,
        teacher.telegram_private_chat_id,
        telegram_username="New_Name",
        sync_username=True,
    )
    assert view is not None
    await db_session.refresh(teacher)
    assert teacher.telegram_username == "New_Name"

    view = await api.login(
        teacher.telegram_user_id,
        teacher.telegram_private_chat_id,
        telegram_username=None,
        sync_username=True,
    )
    assert view is not None
    await db_session.refresh(teacher)
    assert teacher.telegram_username is None

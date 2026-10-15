import re
from datetime import time

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.auth import service as auth_service
from backend.auth import teacher_code_rotation
from backend.auth.exceptions import TeacherCodeGenerationFailed, TeacherCodeNotFound
from backend.auth.models import TeacherProfile, User
from backend.auth.schemas import TeacherPreferences
from backend.auth.teacher_code_rotation import (
    count_teacher_codes_to_rotate,
    rotate_teacher_codes,
)
from backend.telegram.schemas import TeacherRegistration
from backend.telegram.service import register as register_telegram_user
from telegram.invitations import parse_teacher_start_payload


async def _teacher_with_code(session, code: str) -> tuple[User, TeacherProfile]:
    teacher = User(role="TEACHER", timezone="UTC")
    session.add(teacher)
    await session.flush()
    profile = TeacherProfile(teacher_id=teacher.id, code=code)
    session.add(profile)
    await session.flush()
    return teacher, profile


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("t_ABC123", "t_ABC123"),
        ("t_abc123", None),
        ("t_ABC12", None),
        ("t_ABC123x", None),
        ("teacher_legacy-code", "legacy-code"),
        ("t_bad?payload", None),
    ],
)
def test_teacher_start_payload_parsing(payload: str, expected: str | None) -> None:
    assert parse_teacher_start_payload(payload) == expected


async def test_generated_codes_retry_collisions(monkeypatch, db_session) -> None:
    await _teacher_with_code(db_session, "t_ABC123")
    new_teacher = User(role="TEACHER", timezone="UTC")
    db_session.add(new_teacher)
    await db_session.flush()
    codes = iter(["t_ABC123", "t_DEF456"])
    monkeypatch.setattr(auth_service, "generate_teacher_code", lambda: next(codes))

    profile = await auth_service.add_teacher_profile(
        db_session, TeacherPreferences(), new_teacher
    )

    assert profile.code == "t_DEF456"
    assert re.fullmatch(r"t_[A-Z0-9]{6}", profile.code)


async def test_generation_exhaustion_rolls_back_trusted_registration(
    monkeypatch, db_session
) -> None:
    await _teacher_with_code(db_session, "t_ABC123")
    await db_session.commit()
    monkeypatch.setattr(auth_service, "generate_teacher_code", lambda: "t_ABC123")

    with pytest.raises(TeacherCodeGenerationFailed):
        await register_telegram_user(
            db_session,
            TeacherRegistration(
                telegram_user_id=9000000000,
                telegram_private_chat_id=9000000000,
                role="TEACHER",
                timezone="UTC",
            ),
        )

    assert await db_session.scalar(select(func.count()).select_from(User)) == 1
    assert (
        await db_session.scalar(select(func.count()).select_from(TeacherProfile)) == 1
    )


async def test_rotation_is_idempotent_and_preserves_teacher_links(
    monkeypatch, db_session
) -> None:
    first, first_profile = await _teacher_with_code(db_session, "legacy-first")
    _, second_profile = await _teacher_with_code(db_session, "legacy-second")
    unchanged, unchanged_profile = await _teacher_with_code(db_session, "t_KEEP01")
    first_profile.lesson_length = 45
    first_profile.work_start = time(8)
    first_profile.working_days = [1, 3, 5]
    student = User(role="STUDENT", timezone="UTC", teacher_id=first.id)
    db_session.add(student)
    await db_session.commit()

    assert await count_teacher_codes_to_rotate(db_session) == 2
    assert first_profile.code == "legacy-first"
    monkeypatch.setattr(
        auth_service,
        "generate_teacher_code",
        iter(["t_NEW001", "t_NEW002"]).__next__,
    )
    assert await rotate_teacher_codes(db_session) == 2
    await db_session.commit()

    await db_session.refresh(first_profile)
    await db_session.refresh(second_profile)
    await db_session.refresh(unchanged_profile)
    await db_session.refresh(student)
    assert {first_profile.code, second_profile.code} == {"t_NEW001", "t_NEW002"}
    assert re.fullmatch(r"t_[A-Z0-9]{6}", first_profile.code)
    assert re.fullmatch(r"t_[A-Z0-9]{6}", second_profile.code)
    assert first_profile.lesson_length == 45
    assert first_profile.work_start == time(8)
    assert first_profile.working_days == [1, 3, 5]
    assert unchanged_profile.code == "t_KEEP01"
    assert student.teacher_id == first.id
    assert unchanged.id == unchanged_profile.teacher_id
    assert await count_teacher_codes_to_rotate(db_session) == 0
    with pytest.raises(TeacherCodeNotFound):
        await auth_service.resolve_teacher_code(db_session, "legacy-first")


async def test_failed_rotation_leaves_all_codes_unchanged(
    monkeypatch, db_session
) -> None:
    _, first_profile = await _teacher_with_code(db_session, "legacy-first")
    _, second_profile = await _teacher_with_code(db_session, "legacy-second")
    await db_session.commit()
    calls = iter(["t_NEW001", *("t_NEW001" for _ in range(100))])
    monkeypatch.setattr(auth_service, "generate_teacher_code", lambda: next(calls))

    with pytest.raises(RuntimeError, match="no codes were changed"):
        await rotate_teacher_codes(db_session)
    await db_session.rollback()

    await db_session.refresh(first_profile)
    await db_session.refresh(second_profile)
    assert first_profile.code == "legacy-first"
    assert second_profile.code == "legacy-second"


async def test_cli_preview_and_apply_use_disposable_database_transaction(
    monkeypatch, db_session
) -> None:
    _, profile = await _teacher_with_code(db_session, "legacy-code")
    profile_id = profile.id
    await db_session.commit()
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(teacher_code_rotation, "SessionFactory", session_factory)
    monkeypatch.setattr(auth_service, "generate_teacher_code", lambda: "t_NEW001")

    assert await teacher_code_rotation._preview_count() == 1
    async with session_factory() as session:
        unchanged = await session.get(TeacherProfile, profile_id)
        assert unchanged is not None and unchanged.code == "legacy-code"

    with pytest.raises(RuntimeError, match="count changed"):
        await teacher_code_rotation._apply_rotation(2)
    async with session_factory() as session:
        rolled_back = await session.get(TeacherProfile, profile_id)
        assert rolled_back is not None and rolled_back.code == "legacy-code"

    assert await teacher_code_rotation._apply_rotation(1) == 1
    async with session_factory() as session:
        replaced = await session.get(TeacherProfile, profile_id)
        assert replaced is not None and replaced.code == "t_NEW001"

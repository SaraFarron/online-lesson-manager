from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.models import User
from backend.exceptions import ValidationError as BackendValidationError
from backend.homework.models import Homework, HomeworkAttachment, HomeworkSubmission
from backend.homework.schemas import HomeworkCreate, HomeworkSubmissionCreate
from backend.homework.services.assignments import create_assignment
from backend.homework.services.submissions import create_submission


async def _linked_users(session: AsyncSession) -> tuple[User, User]:
    teacher = User(role="TEACHER", timezone="UTC")
    session.add(teacher)
    await session.commit()
    student = User(role="STUDENT", timezone="UTC", teacher_id=teacher.id)
    session.add(student)
    await session.commit()
    return teacher, student


@pytest.mark.parametrize("operation", ["assignment", "submission"])
@pytest.mark.parametrize(
    ("attachment_state", "expected_status"),
    [
        ("missing", 404),
        ("foreign", 404),
        ("expired", 410),
        ("claimed", 404),
        ("cleanup_pending", 404),
    ],
)
async def test_invalid_staged_attachment_states_are_preserved(
    db_session: AsyncSession,
    operation: str,
    attachment_state: str,
    expected_status: int,
) -> None:
    teacher, student = await _linked_users(db_session)
    homework = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        text="Existing assignment",
        operation_key=uuid4(),
    )
    db_session.add(homework)
    await db_session.commit()

    owner = teacher if operation == "assignment" else student
    attachment_id = uuid4()
    attachment: HomeworkAttachment | None = None
    if attachment_state != "missing":
        attachment_owner = owner
        if attachment_state == "foreign":
            attachment_owner = student if operation == "assignment" else teacher
        attachment = HomeworkAttachment(
            id=attachment_id,
            owner_user_id=attachment_owner.id,
            homework_id=homework.id if attachment_state == "claimed" else None,
            storage_key=uuid4().hex,
            file_name="staged.bin",
            byte_size=1,
            staged_until=(
                datetime.now(UTC) - timedelta(seconds=1)
                if attachment_state == "expired"
                else (
                    None
                    if attachment_state == "claimed"
                    else datetime.now(UTC) + timedelta(hours=1)
                )
            ),
            cleanup_pending=attachment_state == "cleanup_pending",
        )
        db_session.add(attachment)
        await db_session.commit()

    with pytest.raises(HTTPException) as raised:
        if operation == "assignment":
            await create_assignment(
                db_session,
                teacher,
                HomeworkCreate(
                    student_id=student.id,
                    text="New assignment",
                    attachment_ids=[attachment_id],
                ),
                uuid4(),
            )
        else:
            await create_submission(
                db_session,
                student,
                homework.id,
                HomeworkSubmissionCreate(
                    text="New submission",
                    attachment_ids=[attachment_id],
                    operation_key=uuid4(),
                ),
            )

    assert raised.value.status_code == expected_status
    assert await db_session.scalar(select(func.count()).select_from(Homework)) == 1
    assert (
        await db_session.scalar(select(func.count()).select_from(HomeworkSubmission))
        == 0
    )
    if attachment is not None:
        await db_session.refresh(attachment)
        assert attachment.homework_id == (
            homework.id if attachment_state == "claimed" else None
        )


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            HomeworkCreate,
            {
                "student_id": uuid4(),
                "text": "Duplicate materials",
                "attachment_ids": [uuid4(), uuid4()],
            },
        ),
        (
            HomeworkSubmissionCreate,
            {
                "text": "Duplicate materials",
                "attachment_ids": [uuid4(), uuid4()],
                "operation_key": uuid4(),
            },
        ),
    ],
)
def test_assignment_and_submission_schemas_reject_duplicate_attachment_ids(
    model: type[BaseModel],
    payload: dict[str, object],
) -> None:
    duplicate_id = uuid4()
    payload["attachment_ids"] = [duplicate_id, duplicate_id]
    with pytest.raises(PydanticValidationError, match="Attachment IDs must be unique"):
        model.model_validate(payload)


@pytest.mark.parametrize(
    ("operation", "expected_detail"),
    [
        ("assignment", "An assignment may contain at most 5 attachments"),
        ("submission", "A submission may contain at most 5 attachments"),
    ],
)
async def test_attachment_count_error_remains_operation_specific(
    db_session: AsyncSession,
    operation: str,
    expected_detail: str,
) -> None:
    teacher, student = await _linked_users(db_session)
    homework = Homework(
        teacher_id=teacher.id,
        student_id=student.id,
        text="Existing assignment",
        operation_key=uuid4(),
    )
    db_session.add(homework)
    await db_session.commit()
    attachment_ids = [uuid4() for _ in range(6)]

    with pytest.raises(BackendValidationError) as raised:
        if operation == "assignment":
            await create_assignment(
                db_session,
                teacher,
                HomeworkCreate.model_construct(
                    student_id=student.id,
                    text="New assignment",
                    attachment_ids=attachment_ids,
                ),
                uuid4(),
            )
        else:
            await create_submission(
                db_session,
                student,
                homework.id,
                HomeworkSubmissionCreate.model_construct(
                    text="New submission",
                    attachment_ids=attachment_ids,
                    operation_key=uuid4(),
                ),
            )

    assert raised.value.detail == expected_detail

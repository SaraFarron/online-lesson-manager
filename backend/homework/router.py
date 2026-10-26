import asyncio
from datetime import UTC
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from starlette.datastructures import UploadFile

from backend.auth.constants import Roles
from backend.auth.dependencies import CurrentUser, SessionDep
from backend.auth.models import User
from backend.exceptions import Forbidden
from backend.homework import (
    assignment_service,
    management_service,
    read_service,
    submission_service,
)
from backend.homework.config import HomeworkConfig, get_homework_settings
from backend.homework.constants import ATTACHMENT_TRANSFER_TIMEOUT_SECONDS
from backend.homework.models import HomeworkAttachment
from backend.homework.schemas import (
    HomeworkCompletionPublic,
    HomeworkCreate,
    HomeworkCreated,
    HomeworkPage,
    HomeworkReadDetail,
    HomeworkStatus,
    HomeworkSubmissionCreate,
    HomeworkSubmissionCreated,
    HomeworkSubmissionPage,
    StagedAttachmentPublic,
)
from backend.homework.service import (
    cancel_staged_attachment,
    open_attachment_download,
    stage_attachment,
    stream_attachment,
)

router = APIRouter(prefix="/homework", tags=["homework"])
HomeworkSettings = Annotated[HomeworkConfig, Depends(get_homework_settings)]


def _require_teacher_or_student(user: User) -> None:
    if user.role not in {Roles.TEACHER.value, Roles.STUDENT.value}:
        raise Forbidden("Only teachers and students can use homework attachments")


@router.post(
    "/assignments",
    response_model=HomeworkCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a homework assignment for an actively linked student",
)
async def create_homework_assignment(
    session: SessionDep,
    user: CurrentUser,
    payload: HomeworkCreate,
    operation_key: Annotated[
        UUID, Header(alias="Idempotency-Key", description="Client-generated UUID")
    ],
) -> HomeworkCreated:
    return await assignment_service.create_assignment(
        session,
        user,
        payload,
        operation_key,
    )


@router.get(
    "/assignments",
    response_model=HomeworkPage,
    summary="List homework visible to the authenticated teacher or student",
)
async def read_homework_assignments(
    session: SessionDep,
    user: CurrentUser,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    assignment_status: Annotated[HomeworkStatus | None, Query(alias="status")] = None,
) -> HomeworkPage:
    return await read_service.list_homework(
        session,
        user,
        skip=skip,
        limit=limit,
        status_filter=assignment_status,
    )


@router.get(
    "/assignments/{homework_id}",
    response_model=HomeworkReadDetail,
    summary="Read a homework assignment and its materials",
)
async def read_homework_assignment(
    session: SessionDep,
    user: CurrentUser,
    homework_id: UUID,
) -> HomeworkReadDetail:
    return await read_service.get_homework_detail(session, user, homework_id)


@router.post(
    "/assignments/{homework_id}/submissions",
    response_model=HomeworkSubmissionCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a homework solution",
)
async def submit_homework(
    session: SessionDep,
    user: CurrentUser,
    homework_id: UUID,
    payload: HomeworkSubmissionCreate,
) -> HomeworkSubmissionCreated:
    return await submission_service.create_submission(
        session,
        user,
        homework_id,
        payload,
    )


@router.get(
    "/assignments/{homework_id}/submissions",
    response_model=HomeworkSubmissionPage,
    summary="Review a homework assignment's student submissions",
)
async def review_homework_submissions(
    session: SessionDep,
    user: CurrentUser,
    homework_id: UUID,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> HomeworkSubmissionPage:
    return await management_service.list_submission_reviews(
        session,
        user,
        homework_id,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/assignments/{homework_id}/complete",
    response_model=HomeworkCompletionPublic,
    summary="Manually mark homework complete",
)
async def complete_homework(
    session: SessionDep,
    user: CurrentUser,
    homework_id: UUID,
) -> HomeworkCompletionPublic:
    return await management_service.complete_homework(session, user, homework_id)


@router.delete(
    "/assignments/{homework_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete homework and its private files",
)
async def delete_homework(
    session: SessionDep,
    user: CurrentUser,
    config: HomeworkSettings,
    homework_id: UUID,
) -> Response:
    await management_service.delete_homework(session, user, homework_id, config)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _staged_attachment_response(
    attachment: HomeworkAttachment,
) -> StagedAttachmentPublic:
    expires_at = attachment.staged_until
    if expires_at is None:
        raise Forbidden("Attachment is no longer staged")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return StagedAttachmentPublic(
        id=attachment.id,
        file_name=attachment.file_name,
        byte_size=attachment.byte_size,
        expires_at=expires_at,
    )


@router.post(
    "/attachments",
    response_model=StagedAttachmentPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a private staged homework attachment",
)
async def upload_attachment(
    session: SessionDep,
    user: CurrentUser,
    config: HomeworkSettings,
    request: Request,
    operation_key: Annotated[
        UUID, Header(alias="Idempotency-Key", description="Client-generated UUID")
    ],
) -> StagedAttachmentPublic:
    _require_teacher_or_student(user)
    try:
        async with asyncio.timeout(ATTACHMENT_TRANSFER_TIMEOUT_SECONDS):
            async with request.form(max_files=1, max_fields=0) as form:
                file = form.get("file")
                if not isinstance(file, UploadFile):
                    raise HTTPException(
                        status_code=422, detail="Exactly one file field is required"
                    )
                attachment = await stage_attachment(
                    session, user, file, operation_key, config
                )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=408, detail="Attachment upload timed out"
        ) from exc
    return _staged_attachment_response(attachment)


@router.get(
    "/attachments/{attachment_id}",
    summary="Download an authorized homework attachment",
    response_class=StreamingResponse,
)
async def download_attachment(
    session: SessionDep,
    user: CurrentUser,
    config: HomeworkSettings,
    attachment_id: UUID,
) -> StreamingResponse:
    _require_teacher_or_student(user)
    download = await open_attachment_download(session, user, attachment_id, config)
    return StreamingResponse(
        stream_attachment(download.file),
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": download.content_disposition,
            "Content-Length": str(download.byte_size),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel an owned staged homework attachment",
)
async def delete_staged_attachment(
    session: SessionDep,
    user: CurrentUser,
    config: HomeworkSettings,
    attachment_id: UUID,
) -> Response:
    _require_teacher_or_student(user)
    await cancel_staged_attachment(session, user, attachment_id, config)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, status

from backend.auth.dependencies import CurrentUser, SessionDep
from backend.notifications import broadcasts
from backend.notifications.schemas import (
    TeacherBroadcastPreview,
    TeacherBroadcastPreviewRequest,
    TeacherBroadcastPublication,
    TeacherBroadcastPublishRequest,
)

router = APIRouter(
    prefix="/notifications/broadcasts",
    tags=["notifications"],
)


@router.post(
    "/preview",
    response_model=TeacherBroadcastPreview,
    summary="Preview a one-time broadcast to the teacher's active students",
)
async def preview_broadcast(
    request: TeacherBroadcastPreviewRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> TeacherBroadcastPreview:
    return await broadcasts.preview_teacher_broadcast(
        session, current_user, request.text
    )


@router.post(
    "",
    response_model=TeacherBroadcastPublication,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue one confirmed teacher broadcast",
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Preview is stale or operation key was reused"
        }
    },
)
async def publish_broadcast(
    request: TeacherBroadcastPublishRequest,
    session: SessionDep,
    current_user: CurrentUser,
    operation_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> TeacherBroadcastPublication:
    return await broadcasts.publish_teacher_broadcast(
        session,
        current_user,
        request,
        operation_key,
    )


@router.get(
    "/operations/{operation_key}",
    response_model=TeacherBroadcastPublication,
    summary="Reconcile a possibly lost broadcast publication response",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Publication not found"}},
)
async def reconcile_broadcast(
    operation_key: UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> TeacherBroadcastPublication:
    return await broadcasts.reconcile_teacher_broadcast(
        session, current_user, operation_key
    )

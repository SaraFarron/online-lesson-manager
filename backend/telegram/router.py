from fastapi import APIRouter, Depends

from backend.auth import service as auth_service
from backend.auth.dependencies import SessionDep
from backend.auth.schemas import Token
from backend.auth.utils import create_access_token
from backend.telegram import service
from backend.telegram.dependencies import authenticate_bot
from backend.telegram.schemas import (
    ErrorResponse,
    TeacherCodeResolve,
    TeacherConfirmation,
    TelegramRegistration,
    TelegramSender,
)

router = APIRouter(
    prefix="/telegram",
    tags=["telegram"],
    dependencies=[Depends(authenticate_bot)],
    responses={
        400: {"model": ErrorResponse, "description": "Inactive account"},
        401: {
            "model": ErrorResponse,
            "description": "Missing or invalid bot-service key",
        },
        404: {"model": ErrorResponse, "description": "Unknown sender or invitation"},
        409: {
            "model": ErrorResponse,
            "description": "Telegram account already registered",
        },
        503: {
            "model": ErrorResponse,
            "description": "Adapter not configured or code generation unavailable",
        },
    },
)


@router.post(
    "/session",
    response_model=Token,
    summary="Issue a user JWT for an existing active Telegram account",
    description=(
        "Trusted bot only. Identity must come from the real Telegram update sender. "
        "Unknown senders return telegram_identity_not_found; no account is created."
    ),
)
async def create_session(session: SessionDep, sender: TelegramSender) -> dict[str, str]:
    user = await service.create_session(session, sender)
    return {"access_token": create_access_token(str(user.id)), "token_type": "bearer"}


@router.post(
    "/register",
    response_model=Token,
    status_code=201,
    summary="Atomically register a passwordless teacher or student",
    description=(
        "Trusted bot only. Teacher codes are server-generated; students must supply "
        "an active teacher's code. Extra identity/privilege fields are forbidden. "
        "Duplicate senders return telegram_already_registered; recover via /session."
    ),
)
async def register(session: SessionDep, data: TelegramRegistration) -> dict[str, str]:
    user = await service.register(session, data)
    return {"access_token": create_access_token(str(user.id)), "token_type": "bearer"}


@router.post(
    "/teacher-code/resolve",
    response_model=TeacherConfirmation,
    summary="Resolve an active teacher invitation for onboarding confirmation",
)
async def resolve_teacher_code(
    session: SessionDep, data: TeacherCodeResolve
) -> dict[str, object]:
    teacher = await auth_service.resolve_teacher_code(session, data.teacher_code)
    return {"teacher_id": teacher.id, "full_name": teacher.full_name}

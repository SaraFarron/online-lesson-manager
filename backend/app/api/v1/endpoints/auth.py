from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DatabaseSession
from app.schemas import UserCreate, UserResponse
from app.services import AuthService

router = APIRouter()


@router.post("/login")
async def login(db: DatabaseSession, token: str) -> dict[str, Any]:
    """User login endpoint."""
    service = AuthService(db)
    response = await service.authenticate_user(token)
    if not response:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    return response


@router.post("/register", response_model=UserResponse)
async def register(db: DatabaseSession, user: UserCreate):
    """User registration endpoint."""
    service = AuthService(db)
    try:
        created_user = await service.register_user(user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    return UserResponse.model_validate(created_user)

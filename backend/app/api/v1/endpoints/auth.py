from fastapi import APIRouter, HTTPException, status

from app.api.deps import DatabaseSession
from app.services import AuthService

router = APIRouter()


@router.post("/login")
async def login(db: DatabaseSession, token: str) -> dict:
    """User login endpoint."""
    service = AuthService(db)
    response = await service.authenticate_user(token)
    if not response:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )
    return response

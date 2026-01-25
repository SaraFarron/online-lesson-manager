from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DatabaseSession
from app.schemas import UserSettingsResponse, UserSettingsUpdate
from app.services import UserSettingsService

router = APIRouter()


@router.get("/settings", response_model=UserSettingsResponse)
async def get_user_settings(
    db: DatabaseSession,
    user: CurrentUser,
) -> UserSettingsResponse:
    """Endpoint for retrieving user settings."""
    service = UserSettingsService(db)
    user_settings = await service.get_user_settings(user)
    if not user_settings:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User has no settings yet"
        )
    return UserSettingsResponse.model_validate(user_settings)


@router.put("/settings", response_model=UserSettingsResponse)
async def update_user_settings(
    db: DatabaseSession,
    user: CurrentUser,
    user_settings: UserSettingsUpdate,
) -> UserSettingsResponse:
    """Endpoint for updating user settings."""
    service = UserSettingsService(db)
    updated_settings = await service.update_user_settings(user, user_settings)
    return UserSettingsResponse.model_validate(updated_settings)

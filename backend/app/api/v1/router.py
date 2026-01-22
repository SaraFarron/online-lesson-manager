from fastapi import APIRouter

from app.api.v1.endpoints import auth, events, health, schedule

api_router = APIRouter()

api_router.include_router(
    health.router,
    prefix="/health",
    tags=["health"],
)

api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["auth"],
)

api_router.include_router(
    schedule.router,
    prefix="/schedule",
    tags=["schedule"],
)

api_router.include_router(
    events.router,
    prefix="/events",
    tags=["events"],
)
